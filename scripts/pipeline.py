#!/usr/bin/env python3
"""Pipeline extremo a extremo, determinista y multi-herramienta.

Cada etapa con modelo se lanza vía scripts/lanzar.py (herramienta y modelo según
agentes.yaml); las etapas mecánicas las ejecuta este script directamente (0 tokens).

  1. validar          estado.py validar (aborta si hay errores)
  2. tracker          [opcional --con-tracker] follow-ups y pendientes REVISAR
  3. buscar           buscador-ofertas (omitible con --sin-busqueda)
  4. adaptar-cv       un agente por oferta prioridad alta sin CV, en paralelo (--max-paralelo)
  5. sincronizar      estado.py sincronizar
  6. drive            drive_sync.py (solo con --drive)
  7. resumen          ofertas nuevas, CVs y follow-ups

Ejemplos:
  python3 scripts/pipeline.py --dry-run
  python3 scripts/pipeline.py --foco finops --max-ofertas 5 --max-paralelo 3 --drive
  python3 scripts/pipeline.py --sin-busqueda             # solo procesa lo pendiente
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lanzar  # noqa: E402

ROOT = lanzar.ROOT
PY = sys.executable


def etapa(n: int, titulo: str):
  print(f"\n{'=' * 70}\n[{n}] {titulo}\n{'=' * 70}", flush=True)


def estado(*args: str, capturar: bool = False) -> subprocess.CompletedProcess:
  return subprocess.run([PY, "scripts/estado.py", *args], cwd=ROOT, text=True,
                        capture_output=capturar)


def pendientes_cv() -> list[dict]:
  res = estado("listar", "--prioridad", "alta", "--sin-cv", "--json", capturar=True)
  return json.loads(res.stdout or "[]")


def ids_actuales() -> set[str]:
  res = estado("listar", "--json", capturar=True)
  return {o["id"] for o in json.loads(res.stdout or "[]")}


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("--foco", default="", help="finops | devsecops | secops-siem | ai-automation")
  p.add_argument("--max-ofertas", type=int, default=0, help="tope de ofertas nuevas para el buscador (0 = sin tope)")
  p.add_argument("--max-paralelo", type=int, default=3)
  p.add_argument("--sin-busqueda", action="store_true")
  p.add_argument("--web", action="store_true", help="ejecuta además búsqueda web con LLM (por defecto solo rastrea ATS oficiales a 0 tokens)")
  p.add_argument("--llm-cv", action="store_true", help="usa agente LLM para redactar CV (por defecto usa ensamblador determinista a 0 tokens)")
  p.add_argument("--con-tracker", action="store_true")
  p.add_argument("--drive", action="store_true", help="sube a Google Drive al final")
  p.add_argument("--dry-run", action="store_true")
  a = p.parse_args()
  inicio = time.time()
  fallos: list[str] = []

  etapa(1, "Validar estado")
  if estado("validar").returncode != 0:
    print("Pipeline abortado: corrige los errores de validación primero.")
    return 1
  antes = ids_actuales()

  if a.con_tracker:
    etapa(2, "Tracker (follow-ups y pendientes)")
    if lanzar.ejecutar("tracker", "Revisión previa al pipeline: follow-ups y avisos REVISAR. No hay novedades de Felipe.",
                       dry_run=a.dry_run):
      fallos.append("tracker")

  if not a.sin_busqueda:
    etapa(3, f"Rastreo directo ATS {a.foco or '(todos los ángulos)'} (0 tokens)")
    cmd_ats = [PY, "scripts/rastrear_ats.py", "--buscar", "--crear-ofertas"]
    if a.foco:
      cmd_ats.extend(["--foco", a.foco])
    if not a.dry_run:
      subprocess.run(cmd_ats, cwd=ROOT)
    else:
      print("[DRY-RUN] Ejecutaría rastreador ATS directo.")

    if a.web:
      etapa("3b", f"Búsqueda web con LLM {a.foco or '(4 ángulos)'}")
      args_busqueda = a.foco
      if a.max_ofertas:
        args_busqueda += f" — guarda como máximo {a.max_ofertas} ofertas nuevas"
      if lanzar.ejecutar("buscar", args_busqueda.strip(), dry_run=a.dry_run):
        fallos.append("buscar")

  etapa(4, "CVs adaptados (prioridad alta sin CV)")
  cola = pendientes_cv()
  if not cola:
    print("No hay ofertas de prioridad alta sin CV.")
  else:
    print(f"{len(cola)} oferta(s) de prioridad alta listas para CV (verifica el ángulo elegido):")
    for o in cola:
      ang = o.get("angulo") or "base"
      sc = o.get("match_score")
      print(f"  · [{sc}/100 | ángulo: {ang:<13}] {o['empresa']} — {o['cargo']}  ({o['id']})")
    print("  (Si algún ángulo no corresponde, corrígelo con: python3 scripts/estado.py mover <id> evaluada --angulo <finops|devsecops|secops-siem|ai-automation>)\n")
    if a.llm_cv:
      with ThreadPoolExecutor(max_workers=max(1, a.max_paralelo)) as pool:
        futuros = {o["id"]: pool.submit(lanzar.ejecutar, "adaptar-cv", o["id"], None, None, a.dry_run, False,
                                        f"cv:{o['empresa'][:20]}") for o in cola}
        for oid, fut in futuros.items():
          if fut.result() != 0:
            fallos.append(f"adaptar-cv {oid}")
    else:
      for o in cola:
        if a.dry_run:
          print(f"[DRY-RUN] Ensamblaría CV para {o['id']} (ángulo: {o.get('angulo') or 'base'})")
        else:
          res_cv = subprocess.run([PY, "scripts/ensamblar_cv.py", "--oferta", o["id"]], cwd=ROOT)
          if res_cv.returncode != 0:
            fallos.append(f"ensamblar-cv {o['id']}")

  etapa(5, "Sincronizar estado")
  if not a.dry_run and estado("sincronizar").returncode != 0:
    fallos.append("sincronizar")

  if a.drive:
    etapa(6, "Google Drive")
    cmd = [PY, "scripts/drive_sync.py"] + (["--dry-run"] if a.dry_run else [])
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
      fallos.append("drive")

  etapa(7, "Resumen")
  nuevas = sorted(ids_actuales() - antes)
  print(f"Ofertas nuevas: {len(nuevas)}")
  for oid in nuevas:
    print(f"  · {oid}")
  estado("listar", "--estado", "cv_generado")
  print(f"\nDuración: {time.time() - inicio:.0f}s · Logs en logs/ · Revisa cambios con `git diff --stat`")
  if fallos:
    print(f"⚠️  Etapas con error: {', '.join(fallos)}")
    return 1
  print("✓ Pipeline completo")
  return 0


if __name__ == "__main__":
  sys.exit(main())
