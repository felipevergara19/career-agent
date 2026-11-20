#!/usr/bin/env python3
"""
Ensamblador dinámico de CVs ATS (Markdown + HTML + PDF vía render_cv.py).

Construye el CV directamente desde el perfil real del usuario
(`perfil/perfil-estructurado.json`, o fallback desde `perfil/perfil-maestro.md`).
- Reordena skills y destaca logros/bullets según el ángulo y las keywords de la oferta.
- Si se indica `--usar-ia` y hay un proveedor de IA configurado, adapta el titular,
  resumen y redacción de bullets al idioma y cargo de la oferta respetando
  veracidad estricta (cero invención).
- Soporta `--base` para generar el CV Base oficial en `cvs-generados/base/`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RENDER_SCRIPT = ROOT / ".agents" / "skills" / "adaptador-cv" / "scripts" / "render_cv.py"
STRUCTURED_PATH = ROOT / "perfil" / "perfil-estructurado.json"
PERFIL_MD_PATH = ROOT / "perfil" / "perfil-maestro.md"


def _slug(text: str, fallback: str = "candidato") -> str:
    s = (text or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or fallback


def _cargar_perfil() -> dict:
    if STRUCTURED_PATH.is_file():
        try:
            data = json.loads(STRUCTURED_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # Fallback mínimo leyendo perfil-maestro.md si aún no hay JSON estructurado
    sys.path.insert(0, str(ROOT / "scripts"))
    import perfil_builder

    return perfil_builder.cargar_perfil_estructurado()


def _priorizar_por_keywords(items: list[str], keywords: list[str]) -> list[str]:
    if not keywords:
        return items
    kws = [k.lower() for k in keywords if k]

    def _score(txt: str) -> int:
        low = txt.lower()
        return sum(1 for k in kws if k in low)

    return sorted(items, key=_score, reverse=True)


def _adaptar_con_ia_si_disponible(
    perfil: dict,
    oferta_meta: dict | None,
    angulo: str,
    idioma: str,
) -> dict | None:
    """Pide al rol `adaptador-cv` adaptar titular, resumen y bullets usando SOLO hechos reales."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import evaluador_ia

    prov = evaluador_ia.detectar_proveedor_ia(rol="adaptador-cv")
    if prov["provider"] == "heuristica":
        return None

    idioma_txt = "English" if idioma == "en" else "Spanish (Español)"
    oferta_ctx = json.dumps(oferta_meta, ensure_ascii=False, indent=2) if oferta_meta else f"Base CV focused on area '{angulo}'"
    perfil_ctx = json.dumps(
        {
            "titular_principal": perfil.get("titular_principal"),
            "resumen_profesional": perfil.get("resumen_profesional"),
            "skills": perfil.get("skills"),
            "experiencias": perfil.get("experiencias"),
            "impact_log": perfil.get("impact_log"),
        },
        ensure_ascii=False,
        indent=2,
    )[:12000]

    prompt = f"""Eres un redactor experto en CVs optimizados para ATS (Applicant Tracking Systems).
Adapta el CV del candidato al idioma **{idioma_txt}** y al objetivo indicado.

REGLA DE VERACIDAD ESTRICTA:
- NUNCA inventes empresas, métricas, años, títulos, ni tecnologías que no estén en el perfil real del candidato.
- Solo reordena, traduce con precisión y redacta en formato ATS los hechos reales proporcionados.

=== PERFIL REAL DEL CANDIDATO ===
{perfil_ctx}

=== OBJETIVO / OFERTA ===
{oferta_ctx}

Responde ÚNICAMENTE con un JSON válido:
{{
  "titular": "Titular ATS de 1 línea en {idioma_txt}",
  "resumen": "Resumen profesional de 3-4 líneas en {idioma_txt} con negritas en logros reales",
  "experiencias": [
    {{
      "empresa": "Nombre exacto",
      "cargo": "Cargo traducido/exacto en {idioma_txt}",
      "periodo": "Periodo exacto",
      "ubicacion": "Ubicación",
      "bullets": ["3-6 bullets ATS en {idioma_txt} priorizando lo más relevante al objetivo"]
    }}
  ]
}}
"""
    try:
        return evaluador_ia.llamar_llm_json(prompt, rol="adaptador-cv")
    except Exception:
        return None


def ensamblar_cv(
    angulo: str = "base",
    idioma: str = "es",
    custom_headline: str | None = None,
    custom_summary: str | None = None,
    oferta_meta: dict | None = None,
    usar_ia: bool = False,
    es_base: bool = False,
) -> tuple[str, str]:
    perfil = _cargar_perfil()
    dp = perfil.get("datos_personales") or {}
    pref = perfil.get("preferencias") or {}
    idioma = "en" if str(idioma).lower().startswith("en") else "es"

    nombre = (dp.get("nombre") or "TU NOMBRE COMPLETO").strip()
    nombre_slug = _slug(nombre, fallback="candidato")

    # Contacto
    partes_contacto = []
    if dp.get("email"):
        partes_contacto.append(f"[{dp['email']}](mailto:{dp['email']})")
    if dp.get("telefono"):
        partes_contacto.append(dp["telefono"])
    if dp.get("linkedin"):
        lbl = re.sub(r"^https?://(www\.)?", "", dp["linkedin"]).rstrip("/")
        partes_contacto.append(f"[{lbl}]({dp['linkedin']})")
    if dp.get("github"):
        lbl = re.sub(r"^https?://(www\.)?", "", dp["github"]).rstrip("/")
        partes_contacto.append(f"[{lbl}]({dp['github']})")
    if dp.get("portafolio"):
        lbl = re.sub(r"^https?://(www\.)?", "", dp["portafolio"]).rstrip("/")
        partes_contacto.append(f"[{lbl}]({dp['portafolio']})")
    contacto_line = " | ".join(partes_contacto) or "email@ejemplo.com"

    ubic_parts = []
    if dp.get("ubicacion"):
        ubic_parts.append(dp["ubicacion"])
    elif dp.get("pais"):
        ubic_parts.append(dp["pais"])
    if dp.get("zona_horaria"):
        ubic_parts.append(dp["zona_horaria"])
    mod = pref.get("modalidad") or "remoto_100"
    if mod == "remoto_100":
        ubic_parts.append("100% Remote" if idioma == "en" else "100% Remoto")
    elif mod == "hibrido":
        ubic_parts.append("Remote / Hybrid" if idioma == "en" else "Remoto / Híbrido")
    idiomas_list = [
        f"{i.get('idioma')} ({i.get('nivel', '')})".strip()
        for i in (dp.get("idiomas") or [])
        if isinstance(i, dict) and i.get("idioma")
    ]
    if idiomas_list:
        ubic_parts.append(" · ".join(idiomas_list))
    ubicacion_line = " | ".join(p for p in ubic_parts if p)

    # Keywords objetivo para priorización determinista
    area_obj = next((a for a in (pref.get("areas") or []) if a.get("slug") == angulo), None)
    kws_target: list[str] = []
    if oferta_meta:
        kws_target.extend(oferta_meta.get("keywords") or [])
        if oferta_meta.get("cargo"):
            kws_target.append(str(oferta_meta["cargo"]))
    if area_obj:
        kws_target.extend(area_obj.get("keywords") or [])

    ia_data = _adaptar_con_ia_si_disponible(perfil, oferta_meta, angulo, idioma) if usar_ia else None

    titular = (
        custom_headline
        or (ia_data.get("titular") if ia_data else None)
        or (f"{area_obj['nombre']} — {perfil.get('titular_principal')}" if area_obj and perfil.get("titular_principal") and area_obj["nombre"].lower() not in perfil["titular_principal"].lower() else None)
        or perfil.get("titular_principal")
        or (" · ".join((pref.get("puestos_objetivo") or [])[:2]))
        or "Professional Profile"
    )

    summary = (
        custom_summary
        or (ia_data.get("resumen") if ia_data else None)
        or perfil.get("resumen_profesional")
        or (perfil.get("historia_libre") or "")[:520].strip()
        or ("Results-oriented professional." if idioma == "en" else "Profesional orientado a resultados.")
    )

    # Skills
    sk = perfil.get("skills") or {}
    sk_adv = _priorizar_por_keywords(list(sk.get("avanzado") or []), kws_target)
    sk_int = _priorizar_por_keywords(list(sk.get("intermedio") or []), kws_target)
    sk_lrn = list(sk.get("aprendiendo") or [])

    skills_lines = []
    if sk_adv:
        lbl = "Core & Production Skills" if idioma == "en" else "Competencias Principales"
        skills_lines.append(f"- **{lbl}:** {', '.join(sk_adv)}")
    if sk_int:
        lbl = "Tools & Working Knowledge" if idioma == "en" else "Herramientas y Nivel Intermedio"
        skills_lines.append(f"- **{lbl}:** {', '.join(sk_int)}")
    if sk_lrn:
        lbl = "Continuous Learning" if idioma == "en" else "En Desarrollo"
        skills_lines.append(f"- **{lbl}:** {', '.join(sk_lrn)}")
    if not skills_lines:
        skills_lines.append("- *(Completa tus skills en Mi Perfil)*")

    # Experiencias
    exps = (ia_data.get("experiencias") if ia_data and ia_data.get("experiencias") else None) or (perfil.get("experiencias") or [])
    exp_blocks = []
    for exp in exps:
        emp = exp.get("empresa") or "Empresa"
        cargo = exp.get("cargo") or ""
        periodo = exp.get("periodo") or ""
        ubic = exp.get("ubicacion") or ""
        header = f"### **{emp}**" + (f" — *{cargo}*" if cargo else "")
        sub_parts = [p for p in (ubic, periodo) if p]
        sub = f"*{' | '.join(sub_parts)}*" if sub_parts else ""
        bullets = _priorizar_por_keywords([str(b).strip() for b in (exp.get("bullets") or []) if str(b).strip()], kws_target)
        b_md = "\n".join(f"- {b}" for b in bullets)
        exp_blocks.append("\n".join(p for p in (header, sub, b_md) if p))

    # Si no hay experiencias cargadas pero sí impact_log, mostrar los logros destacados
    if not exp_blocks and (perfil.get("impact_log") or []):
        lbl = "Key Verified Achievements" if idioma == "en" else "Logros Destacados Verificados"
        b_md = "\n".join(
            f"- **{lg['titulo']}** ({lg.get('ahorro', '')}): {lg.get('descripcion', '')}"
            for lg in perfil["impact_log"]
        )
        exp_blocks.append(f"### **{lbl}**\n{b_md}")

    # Educación, certificaciones e idiomas
    edu_lines = []
    for ed in perfil.get("educacion") or []:
        if isinstance(ed, dict) and ed.get("titulo"):
            inst = f" — {ed['institucion']}" if ed.get("institucion") else ""
            per = f" ({ed['periodo']})" if ed.get("periodo") else ""
            edu_lines.append(f"- **{ed['titulo']}**{inst}{per}")
    certs = [
        f"**{c['nombre']}**" + (f" *({c['entidad']}{', ' + c['fecha'] if c.get('fecha') else ''})*" if c.get("entidad") else "")
        for c in (perfil.get("certificaciones") or [])
        if isinstance(c, dict) and c.get("nombre")
    ]
    if certs:
        lbl = "Certifications" if idioma == "en" else "Certificaciones"
        edu_lines.append(f"- **{lbl}:** {', '.join(certs)}")
    if idiomas_list:
        lbl = "Languages" if idioma == "en" else "Idiomas"
        edu_lines.append(f"- **{lbl}:** {', '.join(idiomas_list)}")

    if idioma == "en":
        sec_summary = "## PROFESSIONAL SUMMARY"
        sec_skills = "## CORE SKILLS"
        sec_exp = "## PROFESSIONAL EXPERIENCE"
        sec_edu = "## EDUCATION & CERTIFICATIONS"
    else:
        sec_summary = "## RESUMEN PROFESIONAL"
        sec_skills = "## COMPETENCIAS PRINCIPALES"
        sec_exp = "## EXPERIENCIA PROFESIONAL"
        sec_edu = "## EDUCACIÓN Y CERTIFICACIONES"

    hoy = datetime.date.today().strftime("%Y-%m-%d")
    angulo_clean = _slug(angulo, fallback="base")
    md_parts = [
        "---",
        "tipo: cv",
        f"angulo: {angulo_clean}",
        f"idioma: {idioma}",
        f"base: {'true' if es_base else 'false'}",
        f"generado: '{hoy}'",
        "---",
        "",
        f"# {nombre.upper()}",
        f"**{titular}**",
    ]
    if ubicacion_line:
        md_parts.append(ubicacion_line)
    md_parts.extend([contacto_line, "", "---", "", sec_summary, summary, "", sec_skills, *skills_lines, ""])
    if exp_blocks:
        md_parts.extend([sec_exp, "", "\n\n".join(exp_blocks), ""])
    if edu_lines:
        md_parts.extend([sec_edu, *edu_lines, ""])

    return "\n".join(md_parts), nombre_slug


def main():
    parser = argparse.ArgumentParser(description="Ensamblador dinámico de CVs ATS desde el perfil del usuario.")
    parser.add_argument("--oferta", help="ID o ruta del archivo de la oferta")
    parser.add_argument("--angulo", default="base", help="Slug del área/ángulo objetivo")
    parser.add_argument("--idioma", choices=["en", "es"], default="")
    parser.add_argument("--headline", help="Titular personalizado")
    parser.add_argument("--summary", help="Resumen ejecutivo personalizado")
    parser.add_argument("--output", help="Ruta del archivo Markdown de salida")
    parser.add_argument("--base", action="store_true", help="Generar CV base en cvs-generados/base/")
    parser.add_argument("--usar-ia", action="store_true", help="Usar IA (rol adaptador-cv) para adaptar redacción e idioma")
    parser.add_argument("--render", action="store_true", default=True, help="Renderizar HTML y PDF con render_cv.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    angulo = args.angulo
    idioma = args.idioma or "es"
    oferta_meta = None
    oferta_basename = None

    if args.oferta:
        oferta_path = Path(args.oferta)
        if not oferta_path.exists():
            res = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "estado.py"), "listar", "--json"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            if res.returncode == 0 and res.stdout.strip():
                for o in json.loads(res.stdout):
                    if args.oferta in (o.get("id"), o.get("legacy_id"), o.get("archivo")):
                        oferta_path = ROOT / o["archivo"]
                        break

        if oferta_path.is_file():
            parts = oferta_path.read_text(encoding="utf-8").split("---", 2)
            if len(parts) >= 3:
                oferta_meta = yaml.safe_load(parts[1]) or {}
                angulo = oferta_meta.get("angulo") or angulo
                if not args.idioma:
                    idioma_str = str(oferta_meta.get("idioma", "")).lower()
                    idioma = "en" if "ingl" in idioma_str or "english" in idioma_str else "es"
                oferta_basename = oferta_path.stem

    cv_md, nombre_slug = ensamblar_cv(
        angulo=angulo,
        idioma=idioma,
        custom_headline=args.headline,
        custom_summary=args.summary,
        oferta_meta=oferta_meta,
        usar_ia=args.usar_ia,
        es_base=args.base,
    )

    hoy = datetime.date.today().strftime("%Y-%m-%d")
    if args.output:
        out_file = Path(args.output)
    elif args.base:
        out_dir = ROOT / "cvs-generados" / "base"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"cv-{nombre_slug}-base-{idioma}.md"
    else:
        out_dir = ROOT / "cvs-generados" / hoy
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = oferta_basename or _slug(angulo, "base")
        out_file = out_dir / f"cv-{nombre_slug}-{suffix}.md"

    if args.dry_run:
        print(f"[DRY-RUN] Se generaría CV en: {out_file} (Ángulo: {angulo}, Idioma: {idioma})")
        print("\n" + cv_md[:400] + "\n...")
        return

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(cv_md, encoding="utf-8")
    print(f"✓ CV ensamblado en Markdown: {out_file.relative_to(ROOT)}")

    if args.render and RENDER_SCRIPT.is_file():
        print("⚙ Renderizando HTML y PDF con render_cv.py...")
        res = subprocess.run([sys.executable, str(RENDER_SCRIPT), str(out_file)], cwd=ROOT)
        if res.returncode == 0:
            print("✓ HTML y PDF generados correctamente.")
        else:
            print("⚠ Aviso: Falló renderizado de PDF (puede requerir Chrome/Chromium).")

    if args.base and STRUCTURED_PATH.is_file():
        try:
            data = json.loads(STRUCTURED_PATH.read_text(encoding="utf-8"))
            pdf_rel = out_file.with_suffix(".pdf").relative_to(ROOT).as_posix()
            md_rel = out_file.relative_to(ROOT).as_posix()
            data["cv_base"] = {
                "modo": "generado",
                "ruta": pdf_rel if out_file.with_suffix(".pdf").is_file() else md_rel,
                "doc_id": None,
                "nombre_archivo": out_file.with_suffix(".pdf").name if out_file.with_suffix(".pdf").is_file() else out_file.name,
            }
            STRUCTURED_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    if args.oferta:
        rel_cv = out_file.relative_to(ROOT).as_posix()
        print("⚙ Vinculando CV a oferta con estado.py mover...")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "estado.py"), "mover", args.oferta, "cv_generado", "--cv", rel_cv],
            cwd=ROOT,
        )


if __name__ == "__main__":
    main()
