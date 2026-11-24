#!/usr/bin/env python3
"""Asistente de Postulación — Respuestas de Formulario y Screening Questions.

Responde preguntas de portales (Greenhouse, Lever, Ashby, Workday, LinkedIn, Get on Board)
basándose estrictamente en el contexto de la oferta laboral y la verdad del Perfil Maestro
de Felipe Vergara Peñaloza (zero hallucination, métricas reales de Colektia, FOCP, etc.).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import evaluador_ia  # noqa: E402

PERFIL_PATH = ROOT / "perfil" / "perfil-maestro.md"
PREF_PATH = ROOT / "perfil" / "preferencias.md"
STRUCTURED_PATH = ROOT / "perfil" / "perfil-estructurado.json"
MATERIAL_DIR = ROOT / "postulaciones" / "material"


def buscar_oferta_por_id(oferta_id_o_slug: str) -> tuple[Path | None, dict]:
    """Localiza el archivo .md de una oferta en ofertas/YYYY-MM-DD/ y retorna (path, meta)."""
    oid = oferta_id_o_slug.strip().replace(".md", "")
    for p in ROOT.glob("ofertas/*/*.md"):
        if p.name.startswith("."):
            continue
        try:
            texto = p.read_text(encoding="utf-8")
            if not texto.startswith("---"):
                continue
            parts = texto.split("---", 2)
            meta = yaml.safe_load(parts[1]) or {}
            fid = meta.get("id") or p.stem
            legacy = meta.get("legacy_id")
            if fid == oid or legacy == oid or p.stem == oid or oid in fid:
                meta["_cuerpo"] = parts[2].strip() if len(parts) > 2 else ""
                meta["_path"] = str(p)
                return p, meta
        except Exception:
            continue
    return None, {}


def extraer_preguntas(texto: str) -> list[str]:
    """Divide un bloque de preguntas ingresadas por el usuario en una lista limpia."""
    if not texto or not texto.strip():
        return []

    lineas = [l.strip() for l in texto.strip().splitlines() if l.strip()]
    preguntas: list[str] = []
    buffer: list[str] = []

    patron_numero = re.compile(r"^(\d+[\.\)\-]|•|\*|-)\s+(.*)$")

    for l in lineas:
        m = patron_numero.match(l)
        if m:
            if buffer:
                preguntas.append(" ".join(buffer))
                buffer = []
            buffer.append(m.group(2))
        elif l.endswith("?") or l.startswith("¿"):
            if buffer:
                buffer.append(l)
                preguntas.append(" ".join(buffer))
                buffer = []
            else:
                preguntas.append(l)
        else:
            buffer.append(l)

    if buffer:
        preguntas.append(" ".join(buffer))

    # Si todo quedó en una sola pregunta pero tiene múltiples '?' internos, intentar separar
    if len(preguntas) == 1 and preguntas[0].count("?") > 1:
        partes = re.split(r"(?<=\?)\s+", preguntas[0])
        if len(partes) > 1:
            preguntas = [p.strip() for p in partes if p.strip()]

    return [p for p in preguntas if len(p) >= 3]


def detectar_idioma_preferido(texto_preguntas: str, oferta: dict, idioma_req: str | None = None) -> str:
    """Detecta si se debe responder en inglés ('en') o español ('es')."""
    if idioma_req in ("en", "es"):
        return idioma_req

    q_low = texto_preguntas.lower()
    indicadores_en = ["what", "why", "how", "describe", "experience", "salary", "years", "tell us", "authorized", "notice period"]
    hits_en = sum(1 for w in indicadores_en if re.search(r"\b" + re.escape(w) + r"\b", q_low))

    indicadores_es = ["qué", "cuál", "cuanto", "cuánto", "describe", "experiencia", "pretensión", "renta", "disponibilidad", "coméntanos", "años"]
    hits_es = sum(1 for w in indicadores_es if re.search(r"\b" + re.escape(w) + r"\b", q_low))

    if hits_en > hits_es:
        return "en"
    if hits_es > hits_en:
        return "es"

    of_idioma = str(oferta.get("idioma") or "").lower()
    if "ingl" in of_idioma or "english" in of_idioma:
        return "en"
    return "es"


def responder_heuristicas(pregunta: str, oferta: dict, idioma: str) -> dict:
    """Respuestas de alta calidad basadas en el Perfil Maestro a 0 tokens si no hay API de IA activa."""
    p_low = pregunta.lower()
    empresa = oferta.get("empresa") or "su empresa"
    cargo = oferta.get("cargo") or "este rol"

    # 1. Pretensión de renta / Salario
    if any(k in p_low for k in ["renta", "salario", "sueldo", "salary", "compensation", "rate", "expectation"]):
        if idioma == "en":
            resp = (
                "My expected compensation is in the range of $3,000 to $4,500 USD/month (as an independent contractor / remote B2B), "
                "which is open to discussion based on the total benefits package, scope of responsibilities, and team structure."
            )
        else:
            resp = (
                "Mi expectativa de renta líquida mensual se sitúa en el rango de $2.600.000 a $3.200.000 CLP líquidos (o su equivalente internacional de $3.000 a $4.500 USD/mes en modalidad contractor), "
                "completamente conversable en función del alcance del cargo, responsabilidades y paquete de beneficios ofrecido."
            )
        return {
            "pregunta": pregunta,
            "respuesta": resp,
            "evidencia_perfil": "Preferencia salarial documentada en preferencias.md ($2.6M-$3.2M CLP / $3k-$4.5k USD).",
            "datos_clave": ["$3,000 - $4,500 USD/mes", "$2.6M - $3.2M CLP líquidos", "Modalidad contractor 100% remota"],
        }

    # 2. Disponibilidad / Preaviso / Ubicación
    if any(k in p_low for k in ["disponibilidad", "preaviso", "notice", "start date", "cuándo puedes", "availability", "ubicación", "residencia"]):
        if idioma == "en":
            resp = (
                "I am based in Santiago, Chile, working 100% remotely with full overlap across US EST, CST, and South American timezones (CLT/UTC-3). "
                "I am currently employed at Colektia as a DevSecOps & Cloud FinOps Engineer and can start within a standard 2-week notice period."
            )
        else:
            resp = (
                "Resido en Santiago, Chile, operando en modalidad 100% remota con total compatibilidad de huso horario (CLT/UTC-3, con solapamiento completo con husos EST/CST). "
                "Actualmente me desempeño en Colektia como DevSecOps & Cloud FinOps Engineer y cuento con disponibilidad de ingreso considerando un período de preaviso estándar de 2 semanas."
            )
        return {
            "pregunta": pregunta,
            "respuesta": resp,
            "evidencia_perfil": "Ubicación Santiago Chile, 100% remoto, 2 semanas de aviso en Colektia.",
            "datos_clave": ["Santiago, Chile", "100% Remoto (CLT/EST)", "Preaviso 2 semanas"],
        }

    # 3. FinOps y Ahorro en AWS
    if any(k in p_low for k in ["finops", "cost", "ahorro", "savings", "focp", "billing", "gasto", "presupuesto"]):
        if idioma == "en":
            resp = (
                "I am a FinOps Certified Practitioner (FOCP) actively managing and auditing AWS spend for a 4-account AWS Organization (~$34.8k USD/month primary account) at Colektia. "
                "Key verified outcomes include leading our AWS Compute Savings Plans renewal end-to-end to secure ~$2,500 USD/month in continuous savings at 99.3% utilization, "
                "eradicating 31 stuck ECS Fargate tasks in an infinite retry loop while deploying VPC Endpoints in production (cutting ~22 TB/month of NAT Gateway traffic), "
                "cutting CloudWatch logs expense by ~40%, and right-sizing Redis ElastiCache instances by 50% without performance degradation."
            )
        else:
            resp = (
                "Cuento con la certificación vigente FinOps Certified Practitioner (FOCP) y administro activamente el gasto de una organización AWS de 4 cuentas (cuenta principal de ~$34.800 USD/mes) en Colektia. "
                "Entre mis resultados concretos destacan: liderar la renovación de AWS Compute Savings Plans generando ~$2.500 USD/mes de ahorro continuo con 99,3% de utilización; "
                "diagnosticar una escalada de +145% en NAT Gateway eliminando 31 tasks zombie de ECS Fargate y desplegando VPC Endpoints en producción (ECR, S3, Secrets Manager, CloudWatch Logs); "
                "reducir ~40% el gasto en CloudWatch Logs y ejecutar right-sizing de 50% en clústeres Redis ElastiCache."
            )
        return {
            "pregunta": pregunta,
            "respuesta": resp,
            "evidencia_perfil": "Impact Log FinOps de Colektia: FOCP, $2.5k USD/mes Savings Plans, VPC Endpoints, -40% logs, -50% Redis.",
            "datos_clave": ["FinOps Certified Practitioner (FOCP)", "$2,500 USD/mes ahorro Savings Plans", "4 cuentas AWS (~$34.8k/mes)"],
        }

    # 4. Experiencia en AWS, Cloud y DevSecOps
    if any(k in p_low for k in ["aws", "cloud", "devops", "devsecops", "infraestructura", "infrastructure", "terraform", "seguridad"]):
        if idioma == "en":
            resp = (
                "I have over 3 years of hands-on production experience in AWS and DevSecOps. At Colektia, I manage AWS Organizations (~75 IAM users across 4 accounts), "
                "architected an event-driven incident response pipeline with Terraform, CloudWatch, GuardDuty, EventBridge, SNS, and incident.io routing on-call alerts to Slack, "
                "and executed hardening on a Wazuh 4.14.10 / OpenSearch SIEM cluster, eradicating an active cryptomining incident and reducing disk usage by 70%."
            )
        else:
            resp = (
                "Cuento con más de 3 años de experiencia práctica en producción sobre AWS y DevSecOps. En Colektia gobierno una AWS Organization de 4 cuentas (~75 usuarios IAM), "
                "diseñé una arquitectura event-driven de respuesta a incidentes con Terraform, CloudWatch, GuardDuty, EventBridge, SNS e incident.io conectada a Slack, "
                "y ejecuté hardening y remediación sobre un clúster SIEM Wazuh 4.14.10 con OpenSearch, erradicando incidentes de seguridad y optimizando un 70% el espacio en disco."
            )
        return {
            "pregunta": pregunta,
            "respuesta": resp,
            "evidencia_perfil": "Gobernanza AWS 4 cuentas, pipeline incident.io/Terraform y Wazuh SIEM en Colektia.",
            "datos_clave": ["AWS Organizations (~75 usuarios)", "Event-driven incidents (Terraform/SNS/incident.io)", "SIEM Wazuh / OpenSearch"],
        }

    # 5. ¿Por qué esta empresa / este cargo?
    if any(k in p_low for k in ["why", "por qué", "interés", "motiva", "interest", "join"]):
        if idioma == "en":
            resp = (
                f"I am specifically targeting the {cargo} position at {empresa} because your requirements directly align with what I solve daily in production: "
                "combining rigorous cloud architecture with FinOps financial efficiency and automation. My background operating multi-account AWS environments, "
                "hands-on cost auditing, and automating workflows with Python and AI allows me to deliver immediate impact to your engineering operations."
            )
        else:
            resp = (
                f"Me interesa particularmente la posición de {cargo} en {empresa} porque combina los desafíos que resuelvo a diario en producción: "
                "arquitectura en la nube con gobernanza, optimización financiera continua (FinOps) y automatización. Mi trayectoria administrando organizaciones AWS multi-cuenta, "
                "ejecutando auditorías de costos de alto impacto y automatizando flujos con Python e IA me permite aportar valor y resultados cuantificables desde el primer día."
            )
        return {
            "pregunta": pregunta,
            "respuesta": resp,
            "evidencia_perfil": "Alineación de perfil Cloud FinOps & DevSecOps con la propuesta de valor de la vacante.",
            "datos_clave": [f"Rol: {cargo}", f"Empresa: {empresa}", "Impacto medible desde el día 1"],
        }

    # 6. Fallback general verídico
    if idioma == "en":
        resp = (
            f"As a bilingual (English C1) DevSecOps & Cloud FinOps Engineer (FOCP certified) with an Engineering degree in Computing and Informatics, "
            f"I have proven production experience managing 4-account AWS architectures, reducing infrastructure costs by over $24k USD/year, "
            f"and automating workflows with Python, Terraform, and AI integrations. I am confident I can contribute directly to {empresa}'s technical objectives."
        )
    else:
        resp = (
            f"Como Ingeniero DevSecOps y Cloud FinOps bilingüe (Inglés C1, certificado FOCP) titulado en Ingeniería en Computación e Informática, "
            f"cuento con experiencia comprobada en producción administrando infraestructuras AWS multi-cuenta, liderando planes de ahorro superiores a $24.000 USD/año "
            f"y automatizando operaciones con Python, Terraform e IA. Cuento con las capacidades prácticas para sumarme al equipo de {empresa} y cumplir los objetivos del cargo."
        )
    return {
        "pregunta": pregunta,
        "respuesta": resp,
        "evidencia_perfil": "Resumen ejecutivo verificado del Perfil Maestro de Felipe Vergara Peñaloza.",
        "datos_clave": ["FOCP Certified", "Ingeniería en Computación e Informática (UNAB)", "Bilingüe EN/ES (C1)"],
    }


def responder_con_ia(preguntas: list[str], oferta: dict, idioma: str, logros_grafo: list[dict]) -> list[dict]:
    """Invoca al LLM mediante el rol 'asistente-postulacion' para generar respuestas profundas y verídicas."""
    empresa = oferta.get("empresa") or "la empresa"
    cargo = oferta.get("cargo") or "el rol"
    salario_of = oferta.get("salario") or "Por confirmar"
    cuerpo_of = oferta.get("_cuerpo") or ""

    perfil_raw = PERFIL_PATH.read_text(encoding="utf-8") if PERFIL_PATH.exists() else ""
    pref_raw = PREF_PATH.read_text(encoding="utf-8") if PREF_PATH.exists() else ""

    # Extraer logros de apoyo
    logros_txt = "\n".join([f"- {l.get('titulo')}: {l.get('ahorro', '')} (Skills: {', '.join(l.get('skills_match', []))})" for l in logros_grafo])

    preguntas_str = "\n".join([f"{idx+1}. {q}" for idx, q in enumerate(preguntas)])

    prompt = f"""Eres el Asistente Especialista de Postulación de Felipe Vergara Peñaloza.
Tu tarea es redactar las respuestas para el formulario de postulación del portal de empleo para la siguiente vacante.

DATOS DE LA VACANTE:
- Empresa: {empresa}
- Cargo: {cargo}
- Salario anunciado: {salario_of}
- Descripción / Requisitos:
{cuerpo_of[:3500]}

PREGUNTAS DEL FORMULARIO A RESPONDER:
{preguntas_str}

IDIOMA DE RESPUESTA:
Debes responder en {"INGLÉS (English C1 fluido, natural, profesional)" if idioma == "en" else "ESPAÑOL (profesional, técnico, formal)"}.

PERFIL MAESTRO DE FELIPE (FUENTE DE VERDAD OBLIGATORIA):
{perfil_raw[:4500]}

PREFERENCIAS DEL CANDIDATO:
{pref_raw[:1500]}

LOGROS RELEVANTES DEL GRAFO (GRAPHRAG):
{logros_txt or 'No hay logros directos adicionales en grafo.'}

REGLAS DE ORO DE VERACIDAD (ZERO HALLUCINATION - CRÍTICO):
1. VERACIDAD ESTRICTA: Usa ÚNICAMENTE hechos reales de Felipe.
   - Trabajo actual: DevSecOps & Cloud FinOps Engineer en Colektia (desde mayo 2026; antes Cybersecurity & AI Engineer desde 2024).
   - Logros cuantificables: ~$2,500 USD/mes ahorro Savings Plans (99.3% uso); 31 tasks ECS zombie erradicadas + VPC Endpoints en producción (~22 TB/mes NAT Gateway); -40% logs CloudWatch; -50% Redis ElastiCache; clúster Wazuh 4.14.10 SIEM (-70% disco saturado, erradicación de cryptominer); flujos Python+n8n+LLM (~10.000 llamadas/día).
   - Certificaciones reales: FinOps Certified Practitioner (FOCP), Google Data Analytics, Google Cybersecurity, IBM AI Developer, Scrum Fundamentals.
   - Títulos: Ingeniería en Computación e Informática (UNAB), Traductor-Intérprete Inglés-Español (EATRI).
2. MANEJO DE BRECHAS: Si una pregunta indaga sobre una herramienta que Felipe NO domina o no tiene en producción (ej. GCP, Azure, Kubernetes avanzado a bajo nivel), NUNCA inventar. Ser transparente y pivotar con elegancia hacia su experiencia equivalente en AWS y su comprobada capacidad de autoaprendizaje.
3. SALARIO:
   - En Chile: $2.600.000 a $3.200.000 CLP líquidos/mes.
   - Internacional / USD: $3,000 a $4,500 USD/mes (contractor remoto B2B, conversable).
4. DISPONIBILIDAD: Santiago, Chile. 100% Remoto. Preaviso estándar de 2 semanas en Colektia.
5. ESTILO: Humano, conciso, orientado a resultados (STAR), sin clichés de IA. Cada respuesta debe tener entre 1 y 3 párrafos concisos, lista para ser copiada directamente al portal.

FORMATO DE SALIDA REQUERIDO:
Responde ÚNICAMENTE con un JSON válido con este esquema exacto:
{{
  "respuestas": [
    {{
      "pregunta": "texto exacto de la pregunta 1",
      "respuesta": "texto redactado listo para copiar y pegar en el portal",
      "evidencia_perfil": "resumen breve de qué datos reales del Perfil Maestro sustentan esta respuesta",
      "datos_clave": ["dato o métrica 1", "métrica 2"]
    }}
  ],
  "consejo_postulacion": "recomendación breve de 1 línea para destacar en este portal"
}}
"""
    try:
        res = evaluador_ia.llamar_llm_json(prompt, rol="asistente-postulacion")
        if isinstance(res, dict) and "respuestas" in res and isinstance(res["respuestas"], list):
            return res["respuestas"]
    except Exception as exc:
        print(f"[WARN] Error llamando a LLM para asistente-postulacion ({exc}). Usando fallback verídico.", file=sys.stderr)

    # Fallback a heurística verídica
    return [responder_heuristicas(q, oferta, idioma) for q in preguntas]


def formatear_markdown(oferta: dict, respuestas: list[dict], idioma: str) -> str:
    """Genera la sección Markdown con las respuestas para anexar al archivo de material."""
    hoy = dt.date.today().strftime("%Y-%m-%d")
    empresa = oferta.get("empresa") or "Empresa"
    cargo = oferta.get("cargo") or "Rol"

    lineas = [
        f"## Respuestas a Preguntas del Formulario (Generadas: {hoy})",
        f"*Respuestas adaptadas para {empresa} — {cargo} con verificación estricta sobre el Perfil Maestro de Felipe.*",
        "",
    ]
    for idx, item in enumerate(respuestas, 1):
        q = item.get("pregunta") or f"Pregunta {idx}"
        r = item.get("respuesta") or ""
        ev = item.get("evidencia_perfil") or ""
        dk = item.get("datos_clave") or []

        lineas.append(f"### {idx}. {q}")
        lineas.append("")
        lineas.append(r)
        lineas.append("")
        if dk:
            lineas.append(f"> **Métricas / Evidencia:** {', '.join(dk)} · *{ev}*")
            lineas.append("")
        lineas.append("---")
        lineas.append("")

    return "\n".join(lineas)


def guardar_en_material(oferta_id: str, oferta: dict, md_respuestas: str, idioma: str) -> Path:
    """Guarda o actualiza el archivo en postulaciones/material/<oferta_id>.md."""
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    target = MATERIAL_DIR / f"{oferta_id}.md"
    hoy = dt.date.today().strftime("%Y-%m-%d")

    if target.exists():
        contenido_existente = target.read_text(encoding="utf-8")
        # Si ya existe una sección previa de respuestas del formulario, reemplazarla o anexar
        if "## Respuestas a Preguntas del Formulario" in contenido_existente:
            partes = contenido_existente.split("## Respuestas a Preguntas del Formulario", 1)
            nuevo_contenido = partes[0].rstrip() + "\n\n" + md_respuestas
        else:
            nuevo_contenido = contenido_existente.rstrip() + "\n\n" + md_respuestas
    else:
        empresa = oferta.get("empresa") or "Empresa"
        cargo = oferta.get("cargo") or "Rol"
        url = oferta.get("url") or ""
        header = f"""---
tipo: material
oferta: {oferta_id}
idioma: {idioma}
generado: '{hoy}'
---
# Material de postulación — {empresa} · {cargo}

## Link directo para postular
{url}

"""
        nuevo_contenido = header + md_respuestas

    target.write_text(nuevo_contenido, encoding="utf-8")
    return target


def responder_preguntas_oferta(
    oferta_id_o_slug: str,
    preguntas_raw: str,
    idioma: str = "auto",
    guardar: bool = True,
) -> dict:
    """Función principal: toma una oferta y un bloque de preguntas y genera respuestas verídicas."""
    p_path, oferta = buscar_oferta_por_id(oferta_id_o_slug)
    if not oferta:
        # Si no se encuentra la oferta exacta, crear un dict básico con el ID
        oferta = {
            "id": oferta_id_o_slug,
            "empresa": "Empresa",
            "cargo": "Candidatura",
            "salario": "Por confirmar",
            "idioma": "en" if idioma == "en" else "es",
        }
    oid = oferta.get("id") or oferta_id_o_slug

    preguntas = extraer_preguntas(preguntas_raw)
    if not preguntas:
        return {
            "ok": False,
            "error": "No se detectaron preguntas válidas en el texto ingresado.",
            "respuestas": [],
        }

    idioma_res = detectar_idioma_preferido(preguntas_raw, oferta, idioma_req=idioma)

    # Contexto GraphRAG
    texto_consulta = f"{oferta.get('cargo', '')} {' '.join(preguntas)}"
    logros = evaluador_ia.consultar_grafo_contexto(texto_consulta)

    # Detección de proveedor
    prov = evaluador_ia.detectar_proveedor_ia(rol="asistente-postulacion")
    if prov["provider"] == "heuristica":
        respuestas = [responder_heuristicas(q, oferta, idioma_res) for q in preguntas]
    else:
        respuestas = responder_con_ia(preguntas, oferta, idioma_res, logros)

    md_texto = formatear_markdown(oferta, respuestas, idioma_res)

    archivo_guardado = None
    if guardar and oid:
        p_mat = guardar_en_material(oid, oferta, md_texto, idioma_res)
        archivo_guardado = str(p_mat.relative_to(ROOT))

    return {
        "ok": True,
        "oferta_id": oid,
        "empresa": oferta.get("empresa"),
        "cargo": oferta.get("cargo"),
        "idioma": idioma_res,
        "proveedor_usado": prov["provider"],
        "modelo_usado": prov.get("model"),
        "respuestas": respuestas,
        "markdown": md_texto,
        "guardado_en": archivo_guardado,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oferta", required=True, help="ID o slug de la oferta (ej. 2026-11-23--slug)")
    parser.add_argument("--preguntas", help="Texto de las preguntas o ruta a archivo de texto")
    parser.add_argument("--idioma", choices=["auto", "en", "es"], default="auto", help="Idioma de respuesta")
    parser.add_argument("--no-guardar", action="store_true", help="No guardar en postulaciones/material/")
    parser.add_argument("--json", action="store_true", help="Salida en JSON puro")

    args = parser.parse_args()

    texto_preguntas = args.preguntas or ""
    if os.path.isfile(texto_preguntas):
        texto_preguntas = Path(texto_preguntas).read_text(encoding="utf-8")
    elif not texto_preguntas and not sys.stdin.isatty():
        texto_preguntas = sys.stdin.read()

    if not texto_preguntas.strip():
        print("Error: Proporciona las preguntas mediante --preguntas o entrada estándar.", file=sys.stderr)
        sys.exit(1)

    resultado = responder_preguntas_oferta(
        oferta_id_o_slug=args.oferta,
        preguntas_raw=texto_preguntas,
        idioma=args.idioma,
        guardar=not args.no_guardar,
    )

    if args.json:
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
        return

    if not resultado["ok"]:
        print(f"Error: {resultado.get('error')}", file=sys.stderr)
        sys.exit(1)

    print(f"\n=======================================================")
    print(f"🤖 ASISTENTE DE POSTULACIÓN — {resultado['empresa']} · {resultado['cargo']}")
    print(f"🌐 Idioma: {resultado['idioma'].upper()} · Proveedor: {resultado['proveedor_usado']} ({resultado['modelo_usado']})")
    if resultado.get("guardado_en"):
        print(f"💾 Guardado en: {resultado['guardado_en']}")
    print(f"=======================================================\n")

    for idx, r in enumerate(resultado["respuestas"], 1):
        print(f"❓ [{idx}] {r['pregunta']}")
        print(f"💬 {r['respuesta']}\n")
        if r.get("datos_clave"):
            print(f"   ✓ Datos clave: {', '.join(r['datos_clave'])}")
        print("-" * 50 + "\n")


if __name__ == "__main__":
    main()
