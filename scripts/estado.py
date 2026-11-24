#!/usr/bin/env python3
"""Motor de estado del protocolo multi-agente de career-agent.

Fuente de verdad: el frontmatter YAML de cada `ofertas/YYYY-MM-DD/<slug>.md`.
Todo lo demás (tracker.md, estado-postulaciones.csv, datos-historial.json[ofertas],
CSV/XLSX) es DERIVADO y se regenera con `sincronizar`.

Uso:
  python3 scripts/estado.py validar
  python3 scripts/estado.py sincronizar
  python3 scripts/estado.py listar [--estado E] [--prioridad P] [--sin-cv] [--json]
  python3 scripts/estado.py existe <texto>          # deduplicación por url/empresa/cargo
  python3 scripts/estado.py mover <id> <estado> [--cv RUTA] [--prep RUTA]
                                  [--material RUTA] [--angulo A] [--motivo TXT] [--nota TXT] [--forzar]
  python3 scripts/estado.py followups [--dias 7]    # postulaciones >N días con mensaje listo (0 tokens)
  python3 scripts/estado.py verificar-links [--estado E] [--limit 30]  # detecta avisos 404/cerrados (0 tokens)

Códigos de salida: 0 OK, 1 errores de validación / uso, 2 lock ocupado.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
  import yaml
except ImportError:  # pragma: no cover
  sys.exit("Error: falta PyYAML (pip install pyyaml)")

ROOT = Path(__file__).resolve().parents[1]
OFERTAS_DIR = ROOT / "ofertas"
CVS_DIR = ROOT / "cvs-generados"
POST_DIR = ROOT / "postulaciones"
LOCK_FILE = ROOT / ".locks" / "estado.lock"
ESTADO_CSV = POST_DIR / "estado-postulaciones.csv"
HISTORIAL_JSON = POST_DIR / "datos-historial.json"
TRACKER_MD = POST_DIR / "tracker.md"
EXCEL_SCRIPT = ROOT / ".agents/skills/perfil-tracker/scripts/actualizar_excel.py"

ESTADOS = {
    "evaluada": "🔍 Evaluada",
    "cv_generado": "📄 CV Generado",
    "postulado": "🚀 Postulado",
    "entrevista_hr": "💬 Entrevista HR/Screening",
    "entrevista_tecnica": "🛠️ Entrevista Técnica",
    "oferta": "🏆 Oferta",
    "rechazada": "❌ Rechazada por empresa",
    "descartada": "❌ Descartada",
}
TRANSICIONES = {
    "evaluada": {"cv_generado", "postulado", "descartada"},
    "cv_generado": {"postulado", "descartada"},
    "postulado": {"entrevista_hr", "entrevista_tecnica", "rechazada", "descartada"},
    "entrevista_hr": {"entrevista_tecnica", "oferta", "rechazada", "descartada"},
    "entrevista_tecnica": {"entrevista_tecnica", "oferta", "rechazada", "descartada"},
    "oferta": {"descartada"},
    "rechazada": set(),
    "descartada": {"evaluada"},
}
REQUIERE_CV = {"cv_generado", "postulado", "entrevista_hr", "entrevista_tecnica", "oferta"}
REQUIERE_FECHA_POST = {"postulado", "entrevista_hr", "entrevista_tecnica", "oferta"}
PIPELINE = ["postulado", "entrevista_hr", "entrevista_tecnica", "oferta"]
PRIORIDADES = {
    "alta": "🔥 Prioridad Alta",
    "media": "✅ Prioridad Media",
    "stretch": "⚠️ Stretch",
    "descartada": "❌ Descartada",
}
ANGULOS = {
    "finops": "Ángulo 1: Cloud FinOps & AWS Cost Optimization",
    "devsecops": "Ángulo 2: DevSecOps & AWS Cloud Security",
    "secops-siem": "Ángulo 3: Wazuh SIEM, SecOps & AWS CloudOps",
    "ai-automation": "Ángulo 4: AI Automation, n8n, Python & RAG",
    "ai-evaluator": "Ángulo 5: AI Evaluator, RLHF, Prompt & Coding/Bilingual Trainer (Hourly USD)",
    "base": "CV Maestro Base",
}


def cargar_angulos_dinamicos() -> dict[str, str]:
  ang = dict(ANGULOS)
  p = ROOT / "perfil" / "perfil-estructurado.json"
  if p.is_file():
    try:
      data = json.loads(p.read_text(encoding="utf-8"))
      for a in (data.get("preferencias") or {}).get("areas") or []:
        sl = (a.get("slug") or "").strip().lower()
        if sl:
          ang[sl] = a.get("nombre") or sl
    except Exception:
      pass
  return ang


SLUG_ANGULO_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
CAMPOS_OFERTA = [
    "tipo", "id", "legacy_id", "empresa", "cargo", "url", "portal", "fecha_captura",
    "modalidad", "idioma", "salario", "match_score", "prioridad", "estado", "angulo",
    "cv", "motivo_descarte", "keywords", "brechas", "notas", "fecha_postulacion",
    "actualizado",
]
FOLLOW_UP_DIAS = 7
FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- utilidades
def hoy() -> str:
  return dt.date.today().isoformat()


def rel(path: Path) -> str:
  return path.relative_to(ROOT).as_posix()


def leer_frontmatter(path: Path) -> tuple[dict, str]:
  texto = path.read_text(encoding="utf-8")
  if not texto.startswith("---\n"):
    raise ValueError("sin frontmatter YAML (debe empezar con '---')")
  fin = texto.find("\n---\n", 4)
  if fin == -1:
    raise ValueError("frontmatter sin cierre '---'")
  meta = yaml.safe_load(texto[4:fin]) or {}
  if not isinstance(meta, dict):
    raise ValueError("el frontmatter no es un mapa YAML")
  for k, v in meta.items():  # YAML convierte fechas sin comillas a date
    if isinstance(v, dt.date):
      meta[k] = v.isoformat()
  return meta, texto[fin + 5:]


def escribir_atomico(path: Path, contenido: str, encoding: str = "utf-8") -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
  try:
    with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
      fh.write(contenido)
    os.replace(tmp, path)
  except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise


def escribir_frontmatter(path: Path, meta: dict, cuerpo: str) -> None:
  fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000)
  escribir_atomico(path, f"---\n{fm}---\n{cuerpo}")


@contextmanager
def lock(timeout_s: float = 30.0):
  """Lock exclusivo para que dos agentes (p.ej. paneles de Herdr) no escriban a la vez."""
  LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
  with LOCK_FILE.open("w") as fh:
    inicio = dt.datetime.now()
    while True:
      try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except BlockingIOError:
        if (dt.datetime.now() - inicio).total_seconds() > timeout_s:
          print(f"Error: lock ocupado ({rel(LOCK_FILE)}). Otro agente está escribiendo; reintenta.")
          sys.exit(2)
        import time
        time.sleep(0.5)
    try:
      fh.write(f"{os.getpid()} {dt.datetime.now().isoformat()}\n")
      fh.flush()
      yield
    finally:
      fcntl.flock(fh, fcntl.LOCK_UN)


# ------------------------------------------------------------------- carga
def archivos_oferta() -> list[Path]:
  return sorted(p for p in OFERTAS_DIR.glob("*/*.md") if FECHA_RE.match(p.parent.name))


def cargar_ofertas() -> tuple[list[dict], list[str]]:
  ofertas, errores = [], []
  for path in archivos_oferta():
    try:
      meta, _ = leer_frontmatter(path)
    except (ValueError, yaml.YAMLError) as exc:
      errores.append(f"{rel(path)}: {exc}")
      continue
    meta["_path"] = path
    ofertas.append(meta)
  return ofertas, errores


# --------------------------------------------------------------- validación
def validar_ofertas(ofertas: list[dict]) -> tuple[list[str], list[str]]:
  errores, avisos = [], []
  ids, urls = {}, {}
  cvs_referenciados = set()
  for o in ofertas:
    path: Path = o["_path"]
    r = rel(path)
    faltan = [c for c in CAMPOS_OFERTA if c not in o]
    if faltan:
      errores.append(f"{r}: faltan campos {faltan}")
      continue
    esperado = f"{path.parent.name}--{path.stem}"
    if o["tipo"] != "oferta":
      errores.append(f"{r}: tipo debe ser 'oferta'")
    if o["id"] != esperado:
      errores.append(f"{r}: id '{o['id']}' debe ser '{esperado}' (carpeta--archivo)")
    if o["id"] in ids:
      errores.append(f"{r}: id duplicado con {ids[o['id']]}")
    ids[o["id"]] = r
    if str(o["fecha_captura"]) != path.parent.name:
      errores.append(f"{r}: fecha_captura {o['fecha_captura']} != carpeta {path.parent.name}")
    estado, prio, score = o["estado"], o["prioridad"], o["match_score"]
    if estado not in ESTADOS:
      errores.append(f"{r}: estado '{estado}' inválido; usa {list(ESTADOS)}")
    if prio not in PRIORIDADES:
      errores.append(f"{r}: prioridad '{prio}' inválida; usa {list(PRIORIDADES)}")
    angulos_validos = cargar_angulos_dinamicos()
    if o["angulo"] is not None and o["angulo"] not in angulos_validos and not SLUG_ANGULO_RE.match(str(o["angulo"])):
      errores.append(f"{r}: angulo '{o['angulo']}' inválido; usa slug minúsculas o null")
    if score is None:
      if estado != "descartada":
        errores.append(f"{r}: match_score null solo se permite en ofertas descartadas")
    elif not isinstance(score, int) or not 0 <= score <= 100:
      errores.append(f"{r}: match_score debe ser entero 0–100")
    if (estado == "descartada") != (prio == "descartada"):
      errores.append(f"{r}: estado 'descartada' y prioridad 'descartada' deben ir juntos")
    if estado == "descartada" and not o["motivo_descarte"]:
      errores.append(f"{r}: falta motivo_descarte")
    if estado in REQUIERE_CV and not o["cv"]:
      errores.append(f"{r}: estado '{estado}' requiere campo cv")
    if estado in REQUIERE_FECHA_POST and not o["fecha_postulacion"]:
      errores.append(f"{r}: estado '{estado}' requiere fecha_postulacion")
    for campo in ("cv", "prep_entrevista", "material"):
      ruta = o.get(campo)
      if ruta:
        if not (ROOT / ruta).is_file():
          errores.append(f"{r}: {campo} apunta a archivo inexistente '{ruta}'")
        elif campo == "cv":
          cvs_referenciados.add(ruta)
    for campo in ("keywords", "brechas"):
      if not isinstance(o[campo], list):
        errores.append(f"{r}: {campo} debe ser lista")
    if isinstance(score, int) and estado != "descartada":
      if prio == "alta" and score < 80:
        avisos.append(f"{r}: prioridad 'alta' con score {score} (<80)")
      if prio == "media" and not 65 <= score <= 79:
        avisos.append(f"{r}: prioridad 'media' con score {score} (fuera de 65–79)")
    if o.get("revisar"):
      avisos.append(f"{r}: REVISAR → {o['revisar']}")
    url = (o["url"] or "").rstrip("/").lower()
    if url:
      if url in urls:
        avisos.append(f"{r}: misma URL que {urls[url]} (¿duplicado o URL genérica?)")
      urls[url] = r

  for cv in sorted(CVS_DIR.glob("*/cv-*.md")):
    r = rel(cv)
    try:
      meta, _ = leer_frontmatter(cv)
    except (ValueError, yaml.YAMLError) as exc:
      errores.append(f"{r}: {exc}")
      continue
    if meta.get("tipo") != "cv":
      errores.append(f"{r}: tipo debe ser 'cv'")
    angulos_validos = cargar_angulos_dinamicos()
    if meta.get("angulo") not in angulos_validos and not SLUG_ANGULO_RE.match(str(meta.get("angulo") or "")):
      errores.append(f"{r}: angulo inválido '{meta.get('angulo')}'")
    if meta.get("idioma") not in ("en", "es"):
      errores.append(f"{r}: idioma debe ser 'en' o 'es'")
    if not cv.with_suffix(".html").exists():
      avisos.append(f"{r}: falta .html (ejecuta render_cv.py)")
    if not meta.get("base") and r not in cvs_referenciados:
      avisos.append(f"{r}: CV no referenciado por ninguna oferta")
  return errores, avisos


def cmd_validar(_args=None, silencioso_ok: bool = False) -> int:
  ofertas, errores = cargar_ofertas()
  e2, avisos = validar_ofertas(ofertas)
  errores += e2
  for a in avisos:
    print(f"[AVISO] {a}")
  for e in errores:
    print(f"[ERROR] {e}")
  if errores:
    print(f"\n✗ {len(errores)} error(es) en {len(ofertas)} ofertas.")
    return 1
  if not silencioso_ok:
    print(f"✓ {len(ofertas)} ofertas válidas ({len(avisos)} aviso(s)).")
  return 0


# ------------------------------------------------------------- derivados
def dias_desde(fecha: str | None) -> int | None:
  if not fecha:
    return None
  return (dt.date.today() - dt.date.fromisoformat(str(fecha))).days


def link(desde: Path, destino: str | Path, etiqueta: str) -> str:
  destino = ROOT / destino if isinstance(destino, str) else destino
  return f"[{etiqueta}]({os.path.relpath(destino, desde).replace(os.sep, '/')})"


def celda(txt) -> str:
  return str(txt if txt is not None else "").replace("|", "/").replace("\n", " ")


def generar_tracker(ofertas: list[dict]) -> str:
  base = TRACKER_MD.parent
  por_score = sorted(ofertas, key=lambda o: (-(o["match_score"] or -1), o["id"]))
  cuenta = lambda pred: sum(1 for o in ofertas if pred(o))
  activas = [o for o in ofertas if o["estado"] not in ("descartada", "rechazada")]
  postuladas = cuenta(lambda o: o["estado"] in PIPELINE or o["estado"] == "rechazada")
  entrevistas = cuenta(lambda o: o["estado"] in ("entrevista_hr", "entrevista_tecnica", "oferta"))

  def fila_cv(o):
    return link(base, o["cv"], Path(o["cv"]).stem.replace("cv-felipe-vergara-", "")) if o["cv"] else "—"

  out = [
      "# Registro Maestro de Postulaciones (Tracker)",
      "",
      "> ⚙️ **ARCHIVO GENERADO — no editar a mano.** Fuente de verdad: frontmatter de `ofertas/*/*.md`.",
      "> Cambia estados con `python3 scripts/estado.py mover <id> <estado>` y regenera con `python3 scripts/estado.py sincronizar`.",
      f"> Última sincronización: {hoy()}",
      "",
      "## 📊 KPIs",
      "",
      "| Métrica | Total |",
      "| :--- | :---: |",
      f"| Ofertas registradas | {len(ofertas)} |",
      f"| Activas (no descartadas/rechazadas) | {len(activas)} |",
      f"| 🔥 Prioridad alta activas | {cuenta(lambda o: o['prioridad'] == 'alta' and o in activas)} |",
      f"| ⚠️ Stretch activas | {cuenta(lambda o: o['prioridad'] == 'stretch' and o in activas)} |",
  ]
  for est, etiqueta in ESTADOS.items():
    out.append(f"| {etiqueta} | {cuenta(lambda o, e=est: o['estado'] == e)} |")
  tasa = f"{entrevistas / postuladas:.0%}" if postuladas else "n/a"
  out += [f"| Conversión postulación → entrevista | {tasa} |", ""]

  pipeline = [o for o in ofertas if o["estado"] in PIPELINE]
  out += ["## 🚀 Pipeline activo (postuladas y en proceso)", ""]
  if pipeline:
    out += ["| ID | Empresa | Cargo | Estado | Postulado | Días | Acción | CV |",
            "| :--- | :--- | :--- | :--- | :--- | :---: | :--- | :--- |"]
    for o in sorted(pipeline, key=lambda o: str(o["fecha_postulacion"])):
      d = dias_desde(o["fecha_postulacion"])
      accion = "⏰ Follow-up" if o["estado"] == "postulado" and d is not None and d > FOLLOW_UP_DIAS else ""
      out.append(f"| {link(base, o['_path'], o['legacy_id'] or o['id'])} | {celda(o['empresa'])} | "
                 f"{celda(o['cargo'])} | {ESTADOS[o['estado']]} | {o['fecha_postulacion']} | {d} | "
                 f"{accion} | {fila_cv(o)} |")
  else:
    out.append("_Aún no hay postulaciones enviadas. Marca una con `estado.py mover <id> postulado`._")
  out.append("")

  def tabla(titulo, filtro, con_notas=True):
    filas = [o for o in por_score if filtro(o)]
    out.extend([f"## {titulo}", ""])
    if not filas:
      out.extend(["_Sin ofertas._", ""])
      return
    out.extend(["| ID | Empresa | Cargo | Match | Estado | Banda Est. | CV | Notas |",
                "| :--- | :--- | :--- | :---: | :--- | :--- | :--- | :--- |"])
    for o in filas:
      out.append(f"| {link(base, o['_path'], o['legacy_id'] or o['id'])} | {celda(o['empresa'])} | "
                 f"{celda(o['cargo'])} | **{o['match_score']}** | {ESTADOS[o['estado']]} | "
                 f"{celda(o['salario'])} | {fila_cv(o)} | {celda(o['notas']) if con_notas else ''} |")
    out.append("")

  no_pipeline = lambda o: o["estado"] in ("evaluada", "cv_generado")
  tabla("🟢 Por postular — Calce real (prioridad alta/media)",
        lambda o: no_pipeline(o) and o["prioridad"] in ("alta", "media"))
  tabla("⚠️ Por postular — Stretch", lambda o: no_pipeline(o) and o["prioridad"] == "stretch")

  revisar = [o for o in ofertas if o.get("revisar")]
  if revisar:
    out += ["## 🔎 Pendientes de revisión", ""]
    out += [f"- {link(base, o['_path'], o['id'])}: {o['revisar']}" for o in revisar]
    out.append("")

  cerradas = [o for o in ofertas if o["estado"] in ("descartada", "rechazada")]
  out += ["## ❌ Descartadas / Rechazadas", ""]
  if cerradas:
    out += ["| ID | Empresa | Cargo | Estado | Motivo |", "| :--- | :--- | :--- | :--- | :--- |"]
    for o in cerradas:
      out.append(f"| {link(base, o['_path'], o['legacy_id'] or o['id'])} | {celda(o['empresa'])} | "
                 f"{celda(o['cargo'])} | {ESTADOS[o['estado']]} | {celda(o['motivo_descarte'])} |")
  else:
    out.append("_Sin ofertas._")
  out.append("")
  return "\n".join(out)


def a_historial(o: dict) -> dict:
  cv_html = re.sub(r"\.md$", ".html", o["cv"]) if o["cv"] else "Ninguno"
  return {
      "id": o["legacy_id"] or o["id"],
      "id_protocolo": o["id"],
      "fecha": o["fecha_captura"],
      "empresa": o["empresa"],
      "cargo": o["cargo"],
      "portal": o["portal"],
      "url": o["url"] or "N/A",
      "modalidad": o["modalidad"],
      "idioma": o["idioma"],
      "salario": o["salario"],
      "match_score": o["match_score"] if o["match_score"] is not None else 0,
      "prioridad": PRIORIDADES[o["prioridad"]],
      "estado": ESTADOS[o["estado"]],
      "cv_usado": cv_html,
      "angulo_cv": ANGULOS.get(o["angulo"], "N/A"),
      "keywords": o["keywords"],
      "brechas": o["brechas"],
      "notas": o["notas"] or (o["motivo_descarte"] or ""),
  }


def cmd_sincronizar(_args=None) -> int:
  with lock():
    if cmd_validar(silencioso_ok=True):
      print("Sincronización abortada: corrige los errores primero.")
      return 1
    ofertas, _ = cargar_ofertas()
    ofertas.sort(key=lambda o: (o["fecha_captura"], o["id"]))

    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "legacy_id", "fecha_captura", "empresa", "cargo", "match_score", "prioridad",
            "estado", "angulo", "cv", "fecha_postulacion", "actualizado", "url", "archivo"]
    w.writerow(cols)
    for o in ofertas:
      w.writerow([o.get(c) if c != "archivo" else rel(o["_path"]) for c in cols])
    escribir_atomico(ESTADO_CSV, buf.getvalue())

    historial = json.loads(HISTORIAL_JSON.read_text(encoding="utf-8")) if HISTORIAL_JSON.exists() else {}
    historial["ultima_actualizacion"] = hoy()
    historial["ofertas"] = [a_historial(o) for o in ofertas]
    historial.setdefault("radar_skills", [])
    historial.setdefault("efectividad_portales", [])
    escribir_atomico(HISTORIAL_JSON, json.dumps(historial, ensure_ascii=False, indent=2) + "\n")

    escribir_atomico(TRACKER_MD, generar_tracker(ofertas))

    res = subprocess.run([sys.executable, str(EXCEL_SCRIPT)], capture_output=True, text=True)
    if res.returncode != 0:
      print(f"[ERROR] actualizar_excel.py falló:\n{res.stderr}")
      return 1
    print(f"✓ Sincronizado: {len(ofertas)} ofertas → {rel(ESTADO_CSV)}, {rel(HISTORIAL_JSON)}, "
          f"{rel(TRACKER_MD)}, XLSX/CSV")
    return 0


# -------------------------------------------------------- consultas / mover
def buscar_oferta(ofertas: list[dict], ident: str) -> dict | None:
  for o in ofertas:
    if ident in (o.get("id"), o.get("legacy_id")) or ident == rel(o["_path"]):
      return o
  return None


def cmd_listar(args) -> int:
  ofertas, errores = cargar_ofertas()
  for e in errores:
    print(f"[ERROR] {e}")
  filas = [o for o in ofertas
           if (not args.estado or o.get("estado") == args.estado)
           and (not args.prioridad or o.get("prioridad") == args.prioridad)
           and (not args.sin_cv or not o.get("cv"))]
  filas.sort(key=lambda o: -(o.get("match_score") or -1))
  if args.json:
    print(json.dumps([{k: v for k, v in o.items() if k != "_path"} | {"archivo": rel(o["_path"])}
                      for o in filas], ensure_ascii=False))
    return 1 if errores else 0
  for o in filas:
    print(f"{o.get('id')}\t{o.get('legacy_id') or '-'}\t{o.get('match_score')}\t"
          f"{o.get('prioridad')}\t{o.get('estado')}\t{o.get('empresa')} — {o.get('cargo')}")
  return 1 if errores else 0


def cmd_existe(args) -> int:
  q = args.texto.lower().rstrip("/")
  ofertas, _ = cargar_ofertas()
  hits = [o for o in ofertas if any(q in str(o.get(c) or "").lower() for c in ("url", "empresa", "cargo", "id"))]
  for o in hits:
    print(f"{o['id']}\t{o.get('estado')}\t{o.get('empresa')} — {o.get('cargo')}\t{o.get('url')}")
  if not hits:
    print("Sin coincidencias.")
  return 0 if hits else 1


def cmd_mover(args) -> int:
  if args.estado not in ESTADOS:
    print(f"Error: estado '{args.estado}' inválido; usa {list(ESTADOS)}")
    return 1
  with lock():
    ofertas, _ = cargar_ofertas()
    o = buscar_oferta(ofertas, args.id)
    if not o:
      print(f"Error: no existe la oferta '{args.id}' (usa `estado.py listar`)")
      return 1
    path = o["_path"]
    meta, cuerpo = leer_frontmatter(path)
    actual = meta["estado"]
    if args.estado != actual and args.estado not in TRANSICIONES.get(actual, set()) and not args.forzar:
      print(f"Error: transición {actual} → {args.estado} no permitida "
            f"(permitidas: {sorted(TRANSICIONES.get(actual, set()))}). Usa --forzar si es intencional.")
      return 1
    if args.cv:
      if not (ROOT / args.cv).is_file():
        print(f"Error: CV inexistente '{args.cv}'")
        return 1
      meta["cv"] = args.cv
    for opcion, campo in (("prep", "prep_entrevista"), ("material", "material")):
      ruta = getattr(args, opcion)
      if ruta:
        if not (ROOT / ruta).is_file():
          print(f"Error: {campo} apunta a archivo inexistente '{ruta}'")
          return 1
        meta[campo] = ruta
    if args.angulo:
      if args.angulo not in ANGULOS:
        print(f"Error: angulo '{args.angulo}' inválido; usa {list(ANGULOS)}")
        return 1
      meta["angulo"] = args.angulo
    if args.estado in REQUIERE_CV and not meta.get("cv"):
      print(f"Error: '{args.estado}' requiere CV; pasa --cv cvs-generados/.../cv-....md")
      return 1
    if args.estado in REQUIERE_FECHA_POST and not meta.get("fecha_postulacion"):
      meta["fecha_postulacion"] = hoy()
    if args.estado == "descartada":
      if not (args.motivo or meta.get("motivo_descarte")):
        print("Error: descartar requiere --motivo")
        return 1
      meta["prioridad"] = "descartada"
    if args.motivo:
      meta["motivo_descarte"] = args.motivo
    if args.nota:
      previa = (meta.get("notas") or "").strip()
      meta["notas"] = f"{previa}\n[{hoy()}] {args.nota}".strip()
    meta["estado"] = args.estado
    meta["actualizado"] = hoy()
    escribir_frontmatter(path, meta, cuerpo)
    print(f"✓ {meta['id']}: {actual} → {args.estado}")
  return 0


def cmd_followups(args) -> int:
  """Lista postulaciones en estado 'postulado' con >= N días sin respuesta y genera
  un mensaje corto y humano listo para copiar/pegar (0 tokens)."""
  hoy_d = dt.date.today()
  ofertas, _ = cargar_ofertas()
  pendientes = []
  for meta in ofertas:
    if meta.get("estado") != "postulado":
      continue
    fp = meta.get("fecha_postulacion") or meta.get("actualizado")
    if not fp:
      continue
    try:
      dias = (hoy_d - dt.date.fromisoformat(str(fp))).days
    except ValueError:
      continue
    if dias >= args.dias:
      pendientes.append((dias, meta))
  pendientes.sort(key=lambda x: x[0], reverse=True)
  if not pendientes:
    print(f"✓ Sin follow-ups pendientes (>= {args.dias} días en estado 'postulado').")
    return 0
  print(f"⏰ {len(pendientes)} postulación(es) con >= {args.dias} días sin respuesta:\n")
  for dias, m in pendientes:
    emp = m.get("empresa") or "el equipo"
    cargo = m.get("cargo") or "el cargo"
    idioma = str(m.get("idioma") or "").lower()
    es_en = "ingl" in idioma or "english" in idioma
    if es_en:
      msg = (
          f"Hi {emp} team, following up on my application for {cargo} from {m.get('fecha_postulacion')}. "
          f"Still very interested in the role and happy to share any extra context on my AWS/FinOps work if helpful. Best, Felipe"
      )
    else:
      msg = (
          f"Hola equipo de {emp}, hago un breve seguimiento a mi postulación para {cargo} del {m.get('fecha_postulacion')}. "
          f"Sigo muy interesado en el rol y feliz de compartir más detalle de mi experiencia en AWS/FinOps si sirve. Saludos, Felipe"
      )
    print(f"• [{dias}d] {m['id']} · {emp} — {cargo}")
    print(f"  Link: {m.get('url')}")
    print(f"  Mensaje: {msg}\n")
  return 0


def cmd_verificar_links(args) -> int:
  """Verifica HTTP + APIs oficiales (Ashby, Greenhouse, Lever, Mercor, micro1) en paralelo (0 tokens)
  si las ofertas siguen abiertas o dieron 404/cerrada. Con --auto-descartar mueve las caídas en
  evaluada/cv_generado a descartada automáticamente."""
  import concurrent.futures as cf
  import urllib.error
  import urllib.request

  re_cerrada = re.compile(
      r"(no longer accepting|no longer available|job (not found|closed|expired|has been filled)|"
      r"position has been filled|this (job|position|listing|opportunity) is (closed|no longer)|"
      r"applications? (are |is )?closed|esta oferta (ha finalizado|ya no está disponible|fue cerrada)|"
      r"the page you are looking for)",
      re.I,
  )
  headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
  estados_obj = {args.estado} if args.estado else {"evaluada", "cv_generado"}
  ofertas, _ = cargar_ofertas()
  objetivos = [m for m in ofertas if m.get("estado") in estados_obj and str(m.get("url") or "").startswith("http")]
  if args.limit:
    objetivos = objetivos[: args.limit]
  if not objetivos:
    print("✓ No hay ofertas activas con URL para verificar.")
    return 0

  # Cache de APIs oficiales para no pedir el mismo tablero 2 veces
  ashby_cache: dict[str, set[str]] = {}
  mercor_cache: dict[str, dict] = {}
  micro1_cache: set[str] | None = None
  cache_lock = cf.thread. threading.Lock() if hasattr(cf, "thread") else None
  import threading
  tlock = threading.Lock()

  def _ashby_ids(slug: str) -> set[str] | None:
    with tlock:
      if slug in ashby_cache:
        return ashby_cache[slug]
    try:
      req = urllib.request.Request(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=headers)
      with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8", errors="ignore"))
        ids = {str(j.get("id")) for j in (data.get("jobs") or [])}
      with tlock:
        ashby_cache[slug] = ids
      return ids
    except Exception:
      return None

  def _mercor_map() -> dict[str, dict]:
    with tlock:
      if mercor_cache:
        return mercor_cache
    try:
      req = urllib.request.Request("https://work.mercor.com/explore", headers=headers)
      with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read().decode("utf-8", errors="ignore")
      m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
      out: dict[str, dict] = {}
      if m:
        data = json.loads(m.group(1))
        for q in (data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries") or []):
          st = (q.get("state") or {}).get("data")
          if isinstance(st, dict) and isinstance(st.get("listings"), list):
            for item in st["listings"]:
              if item.get("listingId"):
                out[str(item["listingId"])] = item
            break
      with tlock:
        mercor_cache.update(out)
      return mercor_cache
    except Exception:
      return {}

  def _micro1_ids() -> set[str] | None:
    nonlocal micro1_cache
    with tlock:
      if micro1_cache is not None:
        return micro1_cache
    try:
      ids: set[str] = set()
      for page in (1, 2, 3, 4):
        req = urllib.request.Request(
            f"https://prod-api.micro1.ai/api/v1/job/portal?page={page}&limit=100&keyword=",
            data=json.dumps({"action": "get_all_jobs", "filters": {"type": ["EXPERT"]}}).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as r:
          data = json.loads(r.read().decode("utf-8", errors="ignore"))
          batch = data.get("data") or []
          for j in batch:
            u = str(j.get("apply_url") or "")
            m_id = re.search(r"/post/([a-f0-9-]{20,})", u)
            if m_id:
              ids.add(m_id.group(1))
          if len(batch) < 100:
            break
      with tlock:
        micro1_cache = ids
      return ids
    except Exception:
      return None

  def _check(m: dict) -> tuple[dict, str, str]:
    url = str(m["url"])
    # 1. Ashby API check
    m_ash = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([a-f0-9-]{20,})", url)
    if m_ash:
      ids = _ashby_ids(m_ash.group(1))
      if ids is not None:
        if m_ash.group(2) not in ids:
          return m, "CERRADA", "No figura en API activa de Ashby"
        return m, "OK", "Activa en API Ashby"

    # 2. Greenhouse API check
    m_gh = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if m_gh:
      try:
        req = urllib.request.Request(
            f"https://boards-api.greenhouse.io/v1/boards/{m_gh.group(1)}/jobs/{m_gh.group(2)}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=8) as r:
          return m, "OK", f"Activa en API Greenhouse (HTTP {r.status})"
      except urllib.error.HTTPError as e:
        if e.code in (404, 410):
          return m, "CERRADA", f"Cerrada en API Greenhouse (HTTP {e.code})"

    # 3. Mercor API check
    m_mc = re.search(r"work\.mercor\.com/jobs/(list_[A-Za-z0-9_-]+)", url)
    if m_mc:
       lid = m_mc.group(1)
       mmap = _mercor_map()
       if mmap:
         item = mmap.get(lid)
         if not item:
           return m, "CERRADA", "Listing ya no existe en Mercor Explore API"
         if item.get("status") != "active" or item.get("disableApplications") or item.get("deletedAt"):
           return m, "CERRADA", f"Mercor status={item.get('status')}, disableApplications={item.get('disableApplications')}"
         slots = item.get("remainingSlots")
         if slots is not None and isinstance(slots, int) and slots <= 0:
           return m, "CERRADA", "Mercor remainingSlots=0 (cupos agotados)"
         return m, "OK", f"Activa en Mercor API (slots={slots if slots is not None else 'abierto'})"

    # 4. micro1 API check
    m_m1 = re.search(r"jobs\.micro1\.ai/post/([a-f0-9-]{20,})", url)
    if m_m1:
      ids = _micro1_ids()
      if ids is not None:
        if m_m1.group(1) not in ids:
          return m, "CERRADA", "Vacante ya no figura en API activa de micro1"
        return m, "OK", "Activa en API micro1"

    # 5. HTTP + Body check general
    try:
      req = urllib.request.Request(url, headers=headers)
      with urllib.request.urlopen(req, timeout=8) as resp:
        body = resp.read(28000).decode("utf-8", errors="ignore")
        if re_cerrada.search(body):
          return m, "CERRADA", f"HTTP {resp.status} (texto de aviso cerrado)"
        return m, "OK", f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
      if e.code in (404, 410):
        return m, "MUERTA", f"HTTP {e.code}"
      return m, "AVISO", f"HTTP {e.code}"
    except Exception as e:
      return m, "AVISO", str(e)[:60]

  muertas, cerradas, avisos, ok_cnt = [], [], [], 0
  with cf.ThreadPoolExecutor(max_workers=10) as ex:
    for m, st, info in ex.map(_check, objetivos):
      if st == "OK":
        ok_cnt += 1
      elif st == "MUERTA":
        muertas.append((m, info))
      elif st == "CERRADA":
        cerradas.append((m, info))
      else:
        avisos.append((m, info))

  print(f"🔎 Verificados {len(objetivos)} links: {ok_cnt} OK · {len(muertas)} 404/410 · {len(cerradas)} cerradas · {len(avisos)} avisos\n")
  descartadas_auto = 0
  for m, info in muertas + cerradas:
    print(f"❌ [{info}] {m['id']} · {m.get('empresa')} — {m.get('cargo')}")
    print(f"   {m.get('url')}")
    if getattr(args, "auto_descartar", False) and m.get("estado") in ("evaluada", "cv_generado"):
      ns = argparse.Namespace(
          id=m["id"],
          estado="descartada",
          cv=None,
          prep=None,
          material=None,
          angulo=None,
          motivo=f"Oferta cerrada o ya no disponible ({info})",
          nota=f"Auto-descartada por verificador de links en vivo ({info})",
          forzar=True,
      )
      if cmd_mover(ns) == 0:
        descartadas_auto += 1
    else:
      print(f"   → python3 scripts/estado.py mover {m['id']} descartada --motivo \"Oferta cerrada o link caído ({info})\"\n")
  if descartadas_auto:
    cmd_sincronizar()
    print(f"✓ Auto-descartadas {descartadas_auto} oferta(s) cerradas/caídas y tablero sincronizado.")
  if avisos:
    print(f"⚠ {len(avisos)} link(s) con bloqueo/timeout (p. ej. LinkedIn/Cloudflare — revisar manualmente si interesa).")
  return 0


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = p.add_subparsers(dest="cmd", required=True)
  sub.add_parser("validar").set_defaults(fn=cmd_validar)
  sub.add_parser("sincronizar").set_defaults(fn=cmd_sincronizar)
  pl = sub.add_parser("listar")
  pl.add_argument("--estado", choices=list(ESTADOS))
  pl.add_argument("--prioridad", choices=list(PRIORIDADES))
  pl.add_argument("--sin-cv", action="store_true")
  pl.add_argument("--json", action="store_true", help="salida JSON (para orquestadores)")
  pl.set_defaults(fn=cmd_listar)
  pe = sub.add_parser("existe")
  pe.add_argument("texto")
  pe.set_defaults(fn=cmd_existe)
  pm = sub.add_parser("mover")
  pm.add_argument("id", help="id del protocolo, legacy_id (OF-013) o ruta del archivo")
  pm.add_argument("estado")
  pm.add_argument("--cv")
  pm.add_argument("--prep")
  pm.add_argument("--material", help="ruta a postulaciones/material/<id>.md")
  pm.add_argument("--angulo", help="finops | devsecops | secops-siem | ai-automation | base")
  pm.add_argument("--motivo")
  pm.add_argument("--nota")
  pm.add_argument("--forzar", action="store_true")
  pm.set_defaults(fn=cmd_mover)
  pf = sub.add_parser("followups", help="postulaciones >N días con mensaje humano listo para copiar")
  pf.add_argument("--dias", type=int, default=7)
  pf.set_defaults(fn=cmd_followups)
  pv = sub.add_parser("verificar-links", help="detecta avisos 404/cerrados en ofertas activas")
  pv.add_argument("--estado", choices=list(ESTADOS), default="")
  pv.add_argument("--limit", type=int, default=50)
  pv.add_argument("--auto-descartar", action="store_true", help="Mueve automáticamente a descartada las ofertas cerradas/404 en evaluada o cv_generado")
  pv.set_defaults(fn=cmd_verificar_links)
  args = p.parse_args()
  return args.fn(args)


if __name__ == "__main__":
  sys.exit(main())
