#!/usr/bin/env python3
"""
Ensamblador dinámico y bilingüe (ES / EN) de CVs ATS (Markdown + HTML + PDF vía render_cv.py).

Construye el CV desde:
  1. Bloques curados bilingües en `perfil/cv-bloques.yaml` (si existe y corresponde al perfil),
     garantizando textos completos sin cortes, 0 tokens, soporte real EN/ES, habilidades blandas
     y veracidad estricta sobre `perfil/perfil-maestro.md`.
  2. Fallback genérico desde `perfil/perfil-estructurado.json` (o `perfil/perfil-maestro.md`)
     para cualquier usuario externo.
  - Si se indica `--usar-ia` y hay proveedor de IA configurado, adapta redacción a la oferta.
  - Preserva la etapa actual de ofertas ya en `postulado`, `entrevista_*` u `oferta` al recrear CV.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RENDER_SCRIPT = ROOT / ".agents" / "skills" / "adaptador-cv" / "scripts" / "render_cv.py"
STRUCTURED_PATH = ROOT / "perfil" / "perfil-estructurado.json"
PERFIL_MD_PATH = ROOT / "perfil" / "perfil-maestro.md"
CV_BLOQUES_PATH = ROOT / "perfil" / "cv-bloques.yaml"

ESTADOS_PRESERVAR = {"cv_generado", "postulado", "entrevista_hr", "entrevista_tecnica", "oferta"}
ESTADOS_ARCHIVADOS = {"descartada", "rechazada"}


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

    sys.path.insert(0, str(ROOT / "scripts"))
    import perfil_builder

    return perfil_builder.cargar_perfil_estructurado()


def _cargar_bloques_curados(perfil: dict) -> dict[str, Any] | None:
    if not CV_BLOQUES_PATH.is_file():
        return None
    try:
        data = yaml.safe_load(CV_BLOQUES_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("angulos"), dict):
        return None
    cand_name = str((data.get("candidato") or {}).get("nombre") or "").strip().lower()
    prof_name = str((perfil.get("datos_personales") or {}).get("nombre") or "").strip().lower()
    if cand_name and prof_name and cand_name.split()[0] != prof_name.split()[0]:
        return None
    return data


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

REGLA DE VERACIDAD ESTRICTA Y ESTILO DEL RESUMEN PROFESIONAL:
- NUNCA inventes empresas, métricas, años, títulos, ni tecnologías que no estén en el perfil real del candidato.
- En el `resumen` (Professional Summary / Resumen Profesional): NUNCA menciones nombres de empresas ("desde 2024 en Colektia"), ni "4 cuentas / ~75 usuarios", ni porcentajes o números sueltos. El resumen debe presentar a Felipe como un profesional integral y bilingüe (EN/ES C1), Ingeniero DevSecOps enfocado en Cloud FinOps (FOCP), con sólida experiencia en ciberseguridad y conocimiento/aplicación práctica de Inteligencia Artificial, nombrando sus herramientas reales en AWS, seguridad (Wazuh SIEM, Bitdefender XDR, IAM, GuardDuty), IaC (Terraform, Ansible, Karpenter, incident.io) y automatización/IA (Python, Bash, n8n, LLMs).
- Solo reordena, traduce con precisión y redacta en formato ATS los hechos reales proporcionados.
- Nunca dejes oraciones ni palabras cortadas.

=== PERFIL REAL DEL CANDIDATO ===
{perfil_ctx}

=== OBJETIVO / OFERTA ===
{oferta_ctx}

Responde ÚNICAMENTE con un JSON válido:
{{
  "titular": "Titular ATS de 1 línea en {idioma_txt}",
  "resumen": "Resumen profesional de 3 líneas completas en {idioma_txt} con negritas en logros reales",
  "experiencias": [
    {{
      "empresa": "Nombre exacto",
      "cargo": "Cargo traducido/exacto en {idioma_txt}",
      "periodo": "Periodo exacto",
      "ubicacion": "Ubicación",
      "bullets": ["3-5 bullets ATS concisos en {idioma_txt} priorizando lo más relevante al objetivo"]
    }}
  ]
}}
"""
    try:
        return evaluador_ia.llamar_llm_json(prompt, rol="adaptador-cv")
    except Exception:
        return None


def _ensamblar_desde_bloques(
    bloques: dict[str, Any],
    angulo: str,
    idioma: str,
    custom_headline: str | None,
    custom_summary: str | None,
    oferta_meta: dict | None,
    ia_data: dict | None,
    es_base: bool,
) -> tuple[str, str]:
    cand = bloques.get("candidato") or {}
    angulos = bloques.get("angulos") or {}
    angulo_key = angulo if angulo in angulos else "base"
    if angulo_key not in angulos:
        angulo_key = next(iter(angulos))
    lang_map = angulos[angulo_key]
    blk = lang_map.get(idioma) or lang_map.get("es") or next(iter(lang_map.values()))

    nombre = str(cand.get("nombre") or "Candidato").strip()
    nombre_slug = _slug(nombre, fallback="candidato")

    partes_contacto = []
    if cand.get("email"):
        partes_contacto.append(f"[{cand['email']}](mailto:{cand['email']})")
    if cand.get("telefono"):
        partes_contacto.append(str(cand["telefono"]))
    if cand.get("linkedin"):
        lbl = re.sub(r"^https?://(www\.)?", "", str(cand["linkedin"])).rstrip("/")
        partes_contacto.append(f"[{lbl}]({cand['linkedin']})")
    if cand.get("github"):
        lbl = re.sub(r"^https?://(www\.)?", "", str(cand["github"])).rstrip("/")
        partes_contacto.append(f"[{lbl}]({cand['github']})")
    contacto_line = " | ".join(partes_contacto)

    ubic_map = cand.get("ubicacion") or {}
    ubicacion_line = ubic_map.get(idioma) if isinstance(ubic_map, dict) else str(ubic_map or "")

    kws_target: list[str] = []
    if oferta_meta:
        kws_target.extend(oferta_meta.get("keywords") or [])
        if oferta_meta.get("cargo"):
            kws_target.append(str(oferta_meta["cargo"]))

    titular = custom_headline or (ia_data.get("titular") if ia_data else None) or blk.get("titular") or ""
    summary = custom_summary or (ia_data.get("resumen") if ia_data else None) or blk.get("resumen") or ""

    skills_lines = []
    for grp in blk.get("skills") or []:
        tags = list(grp.get("tags") or [])
        if tags:
            skills_lines.append(f"- **{grp['label']}:** {', '.join(tags)}")

    soft_list = list((cand.get("soft_skills") or {}).get(idioma) or [])
    exps = (ia_data.get("experiencias") if ia_data and ia_data.get("experiencias") else None) or (blk.get("experiencias") or [])

    exp_blocks = []
    for idx_e, exp in enumerate(exps):
        emp = exp.get("empresa") or "Empresa"
        cargo = exp.get("cargo") or ""
        periodo = exp.get("periodo") or ""
        ubic = exp.get("ubicacion") or ""
        header = f"### **{emp}**" + (f" — *{cargo}*" if cargo else "")
        sub_parts = [p for p in (ubic, periodo) if p]
        sub = f"*{' | '.join(sub_parts)}*" if sub_parts else ""
        raw_bullets = [str(b).strip() for b in (exp.get("bullets") or []) if str(b).strip()]
        # En la primera experiencia, priorizar suavemente solo si la oferta trae keywords específicas
        bullets = _priorizar_por_keywords(raw_bullets, kws_target) if (idx_e == 0 and kws_target and len(raw_bullets) > 1) else raw_bullets
        b_md = "\n".join(f"- {b}" for b in bullets)
        exp_blocks.append("\n".join(p for p in (header, sub, b_md) if p))

    proj = blk.get("proyecto")
    proj_lines = []
    if isinstance(proj, dict) and proj.get("name") and proj.get("highlights"):
        title = f"[{proj['name']}]({proj['url']})" if proj.get("url") else proj["name"]
        date_s = f" ({proj['date']})" if proj.get("date") else ""
        proj_lines.append(f"### **{title}**{date_s}")
        for h in proj["highlights"]:
            proj_lines.append(f"- {h}")

    edu_lines = []
    for ed in (cand.get("educacion") or {}).get(idioma) or []:
        per = f" ({ed['periodo']})" if ed.get("periodo") else ""
        edu_lines.append(f"- **{ed['titulo']}** — {ed['institucion']}{per}")

    certs_raw = (cand.get("certificaciones") or {}).get(idioma) or []
    if certs_raw:
        cert_parts = []
        for c in certs_raw:
            ent = c.get("entidad") or ""
            fec = c.get("fecha") or ""
            tail = f" — {ent}" + (f" ({fec})" if fec else "") if ent else (f" ({fec})" if fec else "")
            cert_parts.append(f"**{c['nombre']}**{tail}")
        lbl_c = "Certifications" if idioma == "en" else "Certificaciones"
        edu_lines.append(f"- **{lbl_c}:** {' | '.join(cert_parts)}")

    langs_raw = (cand.get("idiomas") or {}).get(idioma) or []
    if langs_raw:
        lang_parts = [f"{lg['nombre']} ({lg['nivel']})" for lg in langs_raw if lg.get("nombre")]
        lbl_l = "Languages" if idioma == "en" else "Idiomas"
        edu_lines.append(f"- **{lbl_l}:** {' | '.join(lang_parts)}")

    if idioma == "en":
        sec_summary = "## PROFESSIONAL SUMMARY"
        sec_skills = "## CORE TECHNICAL SKILLS"
        sec_soft = "## SOFT SKILLS"
        sec_exp = "## PROFESSIONAL EXPERIENCE"
        sec_proj = "## FEATURED OPEN-SOURCE PROJECT"
        sec_edu = "## EDUCATION & CERTIFICATIONS"
    else:
        sec_summary = "## RESUMEN PROFESIONAL"
        sec_skills = "## COMPETENCIAS TÉCNICAS PRINCIPALES"
        sec_soft = "## HABILIDADES BLANDAS"
        sec_exp = "## EXPERIENCIA PROFESIONAL"
        sec_proj = "## PROYECTO OPEN-SOURCE DESTACADO"
        sec_edu = "## EDUCACIÓN Y CERTIFICACIONES"

    hoy = datetime.date.today().strftime("%Y-%m-%d")
    angulo_clean = _slug(angulo_key, fallback="base")
    fm: dict[str, Any] = {
        "tipo": "cv",
        "angulo": angulo_clean,
        "idioma": idioma,
        "base": bool(es_base),
        "generado": hoy,
        "kpis": blk.get("kpis") if isinstance(blk.get("kpis"), list) else [],
    }
    fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=1000).strip()

    md_parts = [
        "---",
        fm_yaml,
        "---",
        "",
        f"# {nombre.upper()}",
        f"**{titular}**",
    ]
    if ubicacion_line:
        md_parts.append(ubicacion_line)
    md_parts.extend([contacto_line, "", "---", "", sec_summary, summary, "", sec_skills, *skills_lines, ""])
    if soft_list:
        md_parts.extend([sec_soft, f"- {', '.join(soft_list)}", ""])
    if exp_blocks:
        md_parts.extend([sec_exp, "", "\n\n".join(exp_blocks), ""])
    if proj_lines:
        md_parts.extend([sec_proj, "", "\n".join(proj_lines), ""])
    if edu_lines:
        md_parts.extend([sec_edu, *edu_lines, ""])

    return "\n".join(md_parts), nombre_slug


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
    idioma = "en" if str(idioma).lower().startswith("en") else "es"

    ia_data = _adaptar_con_ia_si_disponible(perfil, oferta_meta, angulo, idioma) if usar_ia else None

    bloques = _cargar_bloques_curados(perfil)
    if bloques is not None:
        return _ensamblar_desde_bloques(
            bloques=bloques,
            angulo=angulo,
            idioma=idioma,
            custom_headline=custom_headline,
            custom_summary=custom_summary,
            oferta_meta=oferta_meta,
            ia_data=ia_data,
            es_base=es_base,
        )

    # Fallback genérico para cualquier usuario externo sin cv-bloques.yaml
    dp = perfil.get("datos_personales") or {}
    pref = perfil.get("preferencias") or {}
    nombre = (dp.get("nombre") or "TU NOMBRE COMPLETO").strip()
    nombre_slug = _slug(nombre, fallback="candidato")

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

    ubic_base = (dp.get("ubicacion") or dp.get("pais") or "").split("(", 1)[0].strip()
    mod = pref.get("modalidad") or "remoto_100"
    mod_txt = ("100% Remote" if idioma == "en" else "100% Remoto") if mod == "remoto_100" else ""
    tz = dp.get("zona_horaria") or ""
    ubicacion_line = " · ".join(p for p in (ubic_base, mod_txt) if p)
    if tz:
        ubicacion_line = f"{ubicacion_line} ({tz})" if ubicacion_line else tz

    idiomas_list = [
        f"{i.get('idioma')} ({i.get('nivel', '')})".strip()
        for i in (dp.get("idiomas") or [])
        if isinstance(i, dict) and i.get("idioma")
    ]

    area_obj = next((a for a in (pref.get("areas") or []) if a.get("slug") == angulo), None)
    kws_target: list[str] = []
    if oferta_meta:
        kws_target.extend(oferta_meta.get("keywords") or [])
        if oferta_meta.get("cargo"):
            kws_target.append(str(oferta_meta["cargo"]))
    if area_obj:
        kws_target.extend(area_obj.get("keywords") or [])

    titular = (
        custom_headline
        or (ia_data.get("titular") if ia_data else None)
        or perfil.get("titular_principal")
        or (" · ".join((pref.get("puestos_objetivo") or [])[:2]))
        or "Professional Profile"
    )
    summary = (
        custom_summary
        or (ia_data.get("resumen") if ia_data else None)
        or perfil.get("resumen_profesional")
        or ("Results-oriented professional." if idioma == "en" else "Profesional orientado a resultados.")
    )

    sk = perfil.get("skills") or {}
    sk_adv = _priorizar_por_keywords(list(sk.get("avanzado") or []), kws_target)
    sk_int = _priorizar_por_keywords(list(sk.get("intermedio") or []), kws_target)
    skills_lines = []
    if sk_adv:
        lbl = "Core & Production Skills" if idioma == "en" else "Competencias Principales"
        skills_lines.append(f"- **{lbl}:** {', '.join(sk_adv[:12])}")
    if sk_int:
        lbl = "Tools & Working Knowledge" if idioma == "en" else "Herramientas Adicionales"
        skills_lines.append(f"- **{lbl}:** {', '.join(sk_int[:10])}")
    if not skills_lines:
        skills_lines.append("- *(Completa tus skills en Mi Perfil)*")

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

    edu_lines = []
    for ed in perfil.get("educacion") or []:
        if isinstance(ed, dict) and ed.get("titulo"):
            inst = f" — {ed['institucion']}" if ed.get("institucion") else ""
            per = f" ({ed['periodo']})" if ed.get("periodo") else ""
            edu_lines.append(f"- **{ed['titulo']}**{inst}{per}")
    certs = [
        f"**{c['nombre']}**" + (f" ({' · '.join(p for p in (c.get('entidad'), c.get('fecha')) if p)})" if (c.get("entidad") or c.get("fecha")) else "")
        for c in (perfil.get("certificaciones") or [])
        if isinstance(c, dict) and c.get("nombre")
    ]
    if certs:
        lbl = "Certifications" if idioma == "en" else "Certificaciones"
        edu_lines.append(f"- **{lbl}:** {' | '.join(certs)}")
    if idiomas_list:
        lbl = "Languages" if idioma == "en" else "Idiomas"
        edu_lines.append(f"- **{lbl}:** {' | '.join(idiomas_list)}")

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


def generar_material_base(oferta_meta: dict, idioma: str, angulo: str) -> Path | None:
    """Genera postulaciones/material/<id>.md a 0 tokens si aún no existe, pre-llenando
    las respuestas repetitivas de formularios (link, salario, timezone, certificaciones, logro clave)."""
    oid = str(oferta_meta.get("id") or "").strip()
    if not oid:
        return None
    mat_dir = ROOT / "postulaciones" / "material"
    mat_dir.mkdir(parents=True, exist_ok=True)
    mat_path = mat_dir / f"{oid}.md"
    if mat_path.exists():
        return mat_path

    empresa = str(oferta_meta.get("empresa") or "la empresa").strip()
    cargo = str(oferta_meta.get("cargo") or "el cargo").strip()
    url = str(oferta_meta.get("url") or "").strip()
    salario = str(oferta_meta.get("salario") or "").strip()
    hoy = datetime.date.today().strftime("%Y-%m-%d")

    logro_por_angulo = {
        "finops": (
            "Led an AWS Compute Savings Plan renewal saving ~$2,500 USD/month (99.3% utilization), traced a +145% NAT Gateway/ECS Fargate spike down to 31 stuck tasks and deployed production VPC Endpoints, and cut CloudWatch/Lambda logging spend by ~40% and ElastiCache by 50%.",
            "Lideré la renovación de AWS Compute Savings Plans ahorrando ~$2.500 USD/mes (99,3% de utilización), diagnostiqué un alza de +145% en NAT Gateway/ECS Fargate eliminando 31 tasks zombie y desplegando VPC Endpoints en producción, y reduje ~40% el gasto en CloudWatch/Lambda y 50% en ElastiCache.",
        ),
        "devsecops": (
            "Designed an event-driven AWS incident pipeline (CloudWatch, GuardDuty, EventBridge, SNS, incident.io) with on-call routing to Slack, enforced IAM least-privilege across a 4-account AWS Organization (~75 users), and deployed VPC Endpoints in production.",
            "Diseñé una arquitectura event-driven de incidentes en AWS (CloudWatch, GuardDuty, EventBridge, SNS, incident.io) con ruteo on-call a Slack, apliqué IAM least-privilege en una AWS Organization de 4 cuentas (~75 usuarios) y desplegué VPC Endpoints en producción.",
        ),
        "secops-siem": (
            "Hardened and remediated a production Wazuh 4.14.10 / OpenSearch SIEM cluster, eradicated a cryptomining incident (devil-miner), reduced disk saturation from ~111 GB down to ~30%, and automated GuardDuty + EventBridge security alerting.",
            "Realicé hardening y remediación en producción de un SIEM Wazuh 4.14.10 / OpenSearch, erradiqué un incidente de criptominería (devil-miner), reduje la saturación de disco de ~111 GB a ~30% y automaticé alertas de seguridad con GuardDuty y EventBridge.",
        ),
        "ai-automation": (
            "Built Python + n8n + LLM automation workflows processing ~10,000 daily calls into BI dashboards, alongside zero-compute local AWS CUR billing audits using DuckDB (httpfs) and Python/Boto3 runbooks.",
            "Construí flujos de automatización con Python, n8n y LLMs procesando ~10.000 llamadas diarias hacia dashboards de BI, además de auditorías de costos AWS CUR a $0 de cómputo cloud con DuckDB local (httpfs) y scripts en Python/Boto3.",
        ),
        "ai-evaluator": (
            "Hold dual degrees in Computer & Information Engineering (B.S.) and English–Spanish Translation (B.A., C1), with hands-on experience working on GPT-4o / Python call-evaluation workflows, a Google Gemini RAG prototype, and building an open-source multi-LLM rubric evaluator with Neo4j GraphRAG.",
            "Cuento con doble titulación en Ingeniería en Computación e Informática y Traducción Inglés–Español (C1), experiencia práctica en flujos de evaluación con GPT-4o y Python, un prototipo RAG con Google Gemini y el desarrollo de un evaluador multi-LLM por rúbricas con Neo4j GraphRAG.",
        ),
    }
    logro_en, logro_es = logro_por_angulo.get(angulo, logro_por_angulo["finops"])

    if idioma == "en":
        sal_txt = salario if (salario and "confirmar" not in salario.lower()) else "$3,000 - $4,000 USD/month (flexible depending on scope and benefits)"
        contenido = f"""---
tipo: material
oferta: {oid}
idioma: en
generado: '{hoy}'
---
# Material de postulación — {empresa} · {cargo}

## Link para postular
{url}

## Cover letter (borrador base — personaliza 1 detalle de {empresa} antes de enviar)
Hi {empresa} team,

I just applied for the {cargo} role. I am a FinOps Certified Practitioner (FOCP) and DevSecOps/AWS Engineer based in Chile (100% remote, EST/CLT overlap), currently managing a 4-account AWS environment at Colektia.

In my current role, I {logro_en[0].lower() + logro_en[1:]}

Happy to share concrete details from those projects if helpful.

Felipe Vergara Peñaloza

## Nota para el recruiter / Hiring Manager en LinkedIn (≤ 260 caracteres)
Hi! Just applied for the {cargo} role at {empresa}. I'm an FOCP-certified AWS DevSecOps/FinOps Engineer (remote from Chile, EST overlap) with hands-on work in AWS cost optimization, IAM, and automation. Happy to connect.

## Respuestas a preguntas frecuentes del formulario
- **Relevant project / achievement:** {logro_en}
- **Certifications & languages:** FinOps Certified Practitioner (FOCP, Nov 2026), B.S. in Computer Science (UNAB, 2026), AWS SAA-C03 in preparation. English C1 (fluent in technical/vendor calls), Spanish native.
- **Salary expectations:** {sal_txt}
- **Notice period / availability / timezone:** Standard 2 weeks notice (negotiable). Based in Santiago, Chile (CLT, UTC-3/-4 — full overlap with US East / EST), 100% remote since 2019.
"""
    else:
        sal_txt = salario if (salario and "confirmar" not in salario.lower()) else "$2.600.000 - $3.200.000 CLP líquidos (o $3.000 - $4.000 USD/mes, conversable según alcance)"
        contenido = f"""---
tipo: material
oferta: {oid}
idioma: es
generado: '{hoy}'
---
# Material de postulación — {empresa} · {cargo}

## Link para postular
{url}

## Cover letter (borrador base — personaliza 1 detalle de {empresa} antes de enviar)
Hola equipo de {empresa},

Acabo de postular al cargo de {cargo}. Soy Ingeniero en Computación e Informática, FinOps Certified Practitioner (FOCP) y DevSecOps/Cloud AWS Engineer en Colektia, donde administro un entorno AWS multi-cuenta (4 cuentas).

En mi rol actual, {logro_es[0].lower() + logro_es[1:]}

Si les resulta útil, feliz de contarles en detalle cómo abordamos esos proyectos.

Felipe Vergara Peñaloza

## Nota para el recruiter / Hiring Manager en LinkedIn (≤ 260 caracteres)
Hola! Acabo de postular a {cargo} en {empresa}. Soy DevSecOps & Cloud FinOps Engineer (FOCP) con foco práctico en AWS, costos cloud, IAM y automatización en Python. Quedo atento por si hace sentido conversar.

## Respuestas a preguntas frecuentes del formulario
- **Proyecto / logro relevante:** {logro_es}
- **Certificaciones e idiomas:** FinOps Certified Practitioner (FOCP, nov 2026), Ingeniería en Computación e Informática (UNAB, 2026), AWS SAA-C03 en preparación. Inglés C1 avanzado, español nativo.
- **Expectativas salariales:** {sal_txt}
- **Disponibilidad / modalidad / zona horaria:** Aviso estándar de 2 semanas (conversable). Ubicado en Santiago, Chile (CLT / overlap completo con EST), trabajando 100% remoto desde 2019.
"""
    mat_path.write_text(contenido, encoding="utf-8")
    return mat_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensamblador dinámico y bilingüe de CVs ATS.")
    parser.add_argument("--oferta", help="ID o ruta del archivo de la oferta")
    parser.add_argument("--angulo", default="", help="Slug del área/ángulo objetivo (sobrescribe el de la oferta si se indica)")
    parser.add_argument("--idioma", choices=["en", "es"], default="")
    parser.add_argument("--headline", help="Titular personalizado")
    parser.add_argument("--summary", help="Resumen ejecutivo personalizado")
    parser.add_argument("--output", help="Ruta del archivo Markdown de salida")
    parser.add_argument("--base", action="store_true", help="Generar CV base en cvs-generados/base/")
    parser.add_argument("--usar-ia", action="store_true", help="Usar IA (rol adaptador-cv) para adaptar redacción e idioma")
    parser.add_argument("--render", action="store_true", default=True, help="Renderizar HTML y PDF con render_cv.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    angulo_explicit = bool(args.angulo)
    angulo = args.angulo or "base"
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
                if not angulo_explicit:
                    angulo = oferta_meta.get("angulo") or angulo
                if not args.idioma:
                    idioma_str = str(oferta_meta.get("idioma", "")).lower()
                    idioma = "en" if ("ingl" in idioma_str or "english" in idioma_str) else "es"
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
        ang_suffix = _slug(angulo, "base")
        out_file = out_dir / f"cv-{nombre_slug}-{ang_suffix}-{idioma}.md"
    else:
        out_dir = ROOT / "cvs-generados" / hoy
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = oferta_basename or _slug(angulo, "base")
        out_file = out_dir / f"cv-{nombre_slug}-{suffix}-{idioma}.md"

    if args.dry_run:
        print(f"[DRY-RUN] Se generaría CV en: {out_file} (Ángulo: {angulo}, Idioma: {idioma})")
        print("\n" + cv_md[:600] + "\n...")
        return 0

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(cv_md, encoding="utf-8")
    print(f"✓ CV ensamblado en Markdown: {out_file.relative_to(ROOT)}")

    if args.render and RENDER_SCRIPT.is_file():
        print("⚙ Renderizando HTML y PDF con render_cv.py...")
        res = subprocess.run([sys.executable, str(RENDER_SCRIPT), str(out_file)], cwd=ROOT)
        if res.returncode == 0:
            print("✓ HTML y PDF generados correctamente.")
        else:
            print("⚠ Error al renderizar PDF.", file=sys.stderr)
            return 1

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
        mat_path = generar_material_base(oferta_meta, idioma, _slug(angulo, "base")) if oferta_meta else None
        if mat_path:
            print(f"✓ Material base disponible en: {mat_path.relative_to(ROOT)}")
        estado_actual = str((oferta_meta or {}).get("estado") or "evaluada")
        # Preservar la etapa actual si ya está en postulado / entrevistas / oferta / archivo
        if estado_actual in ESTADOS_PRESERVAR or estado_actual in ESTADOS_ARCHIVADOS:
            target_estado = estado_actual
        else:
            target_estado = "cv_generado"
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "estado.py"),
            "mover",
            args.oferta,
            target_estado,
            "--cv",
            rel_cv,
        ]
        if mat_path and mat_path.is_file():
            cmd.extend(["--material", mat_path.relative_to(ROOT).as_posix()])
        if angulo_explicit:
            cmd.extend(["--angulo", _slug(angulo, "base")])
        if estado_actual in ESTADOS_ARCHIVADOS:
            cmd.append("--forzar")
        print(f"⚙ Vinculando CV a oferta ({estado_actual} → {target_estado})...")
        mv = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if mv.returncode != 0:
            print(mv.stderr or mv.stdout, file=sys.stderr)
            return mv.returncode
        if mv.stdout.strip():
            print(mv.stdout.strip())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
