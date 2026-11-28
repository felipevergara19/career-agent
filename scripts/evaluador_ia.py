#!/usr/bin/env python3
"""Evaluador de ofertas con IA real + GraphRAG (Neo4j / grafo local).

Aplica estrictamente la rúbrica 0-100 y filtros duros de `perfil/preferencias.md`
cruzándolos contra el Perfil Maestro y los logros relevantes recuperados desde Neo4j.

Proveedores soportados por orden automático:
  1. GEMINI_API_KEY (Google AI Studio — REST API nativa, sin SDK extra)
  2. ANTHROPIC_API_KEY (Anthropic Messages API)
  3. OPENAI_API_KEY (OpenAI Chat Completions API)
  4. OLLAMA_URL (modelos locales Ollama)
  5. agy CLI local (cuando se corre fuera de Docker sin API keys)
  6. Fallback heurístico transparente (NUNCA hardcodea 85; calcula los 5 criterios)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PERFIL_PATH = ROOT / "perfil" / "perfil-maestro.md"
PREF_PATH = ROOT / "perfil" / "preferencias.md"
STRUCTURED_PERFIL_PATH = ROOT / "perfil" / "perfil-estructurado.json"
GRAFO_JSON = ROOT / "postulaciones" / "grafo_conocimiento.json"
AI_CONFIG_PATH = ROOT / "postulaciones" / ".ai-config.json"


def _cargar_perfil_estructurado_safe() -> dict:
    if not STRUCTURED_PERFIL_PATH.is_file():
        return {}
    try:
        data = json.loads(STRUCTURED_PERFIL_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}

ENV_KEYS_MAP = [
    "AI_PROVIDER",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OLLAMA_URL",
    "OLLAMA_MODEL",
]


DEFAULT_ROLES_CONFIG = {
    "perfil-tracker": {
        "nombre": "Perfil & Tracker",
        "tier": "Mecánico · Costo mínimo",
        "descripcion": "Estados, KPIs, follow-ups y actualización del radar.",
        "provider": "",
        "model": "",
    },
    "buscador-ofertas": {
        "nombre": "Buscador & Evaluador",
        "tier": "Volumen · Rúbrica + GraphRAG",
        "descripcion": "Evalúa ofertas 0–100 con filtros Chile/Remoto y logros de Neo4j.",
        "provider": "",
        "model": "",
    },
    "adaptador-cv": {
        "nombre": "Adaptador de CV",
        "tier": "Redacción ATS · Calidad media-alta",
        "descripcion": "Personaliza CVs, cover letters y respuestas de postulación.",
        "provider": "",
        "model": "",
    },
    "preparador-entrevistas": {
        "nombre": "Preparador de Entrevistas",
        "tier": "Razonamiento profundo · Uso puntual",
        "descripcion": "Guías técnicas, historias STAR, defensa de brechas y negociación.",
        "provider": "",
        "model": "",
    },
    "asistente-postulacion": {
        "nombre": "Asistente de Postulación",
        "tier": "Redacción Verídica · Preguntas Portal",
        "descripcion": "Responde screening questions de portales basadas estrictamente en el perfil real de Felipe.",
        "provider": "",
        "model": "",
    },
}


def cargar_config_ia_local() -> dict:
    """Carga configuración guardada desde la UI (postulaciones/.ai-config.json)
    y la inyecta en os.environ si tiene valores definidos."""
    if not AI_CONFIG_PATH.is_file():
        return {}
    try:
        cfg = json.loads(AI_CONFIG_PATH.read_text(encoding="utf-8"))
        for k in ENV_KEYS_MAP:
            v = cfg.get(k)
            if isinstance(v, str) and v.strip():
                os.environ[k] = v.strip()
        return cfg
    except Exception:
        return {}


def obtener_roles_config() -> dict:
    cfg = cargar_config_ia_local()
    saved_roles = cfg.get("roles") if isinstance(cfg.get("roles"), dict) else {}
    out = {}
    for role_key, meta in DEFAULT_ROLES_CONFIG.items():
        merged = dict(meta)
        s = saved_roles.get(role_key) or {}
        if isinstance(s, dict):
            merged["provider"] = (s.get("provider") or "").strip()
            merged["model"] = (s.get("model") or "").strip()
        out[role_key] = merged
    return out


def guardar_config_ia_local(updates: dict) -> dict:
    """Guarda configuración de API keys y matriz multi-agente en postulaciones/.ai-config.json (chmod 600, gitignored)."""
    actual = {}
    if AI_CONFIG_PATH.is_file():
        try:
            actual = json.loads(AI_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            actual = {}

    for k in ENV_KEYS_MAP:
        if k not in updates:
            continue
        val = (updates.get(k) or "").strip()
        # Si es un valor enmascarado del frontend, conservar el valor anterior
        if "..." in val and k.endswith("_KEY"):
            continue
        if val == "__CLEAR__":
            actual.pop(k, None)
            os.environ.pop(k, None)
        elif val:
            actual[k] = val
            os.environ[k] = val
        elif k == "AI_PROVIDER":
            actual.pop(k, None)
            os.environ.pop(k, None)

    if isinstance(updates.get("roles"), dict):
        roles_clean = actual.get("roles") if isinstance(actual.get("roles"), dict) else {}
        for rk in DEFAULT_ROLES_CONFIG:
            if rk in updates["roles"] and isinstance(updates["roles"][rk], dict):
                roles_clean[rk] = {
                    "provider": (updates["roles"][rk].get("provider") or "").strip(),
                    "model": (updates["roles"][rk].get("model") or "").strip(),
                }
        actual["roles"] = roles_clean

    AI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AI_CONFIG_PATH.write_text(json.dumps(actual, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(AI_CONFIG_PATH, 0o600)
    except Exception:
        pass
    return actual


def _leer_api_desde_agentes_yaml(rol: str | None) -> tuple[str, str]:
    """Lee proveedor/modelo API por defecto desde agentes.yaml → roles.<rol>.modelos.api
    (formato 'openrouter/google/gemini-2.5-flash' o 'gemini/gemini-2.5-flash')."""
    if not rol:
        return "", ""
    ag_path = ROOT / "agentes.yaml"
    if not ag_path.is_file():
        return "", ""
    try:
        data = yaml.safe_load(ag_path.read_text(encoding="utf-8")) or {}
        api_spec = str((((data.get("roles") or {}).get(rol) or {}).get("modelos") or {}).get("api") or "").strip()
        if "/" in api_spec:
            prov, mod = api_spec.split("/", 1)
            return prov.strip().lower(), mod.strip()
    except Exception:
        pass
    return "", ""


def detectar_proveedor_ia(rol: str | None = None) -> dict:
    cfg = cargar_config_ia_local()

    candidatos = {
        "openrouter": lambda: bool(os.environ.get("OPENROUTER_API_KEY")),
        "gemini": lambda: bool(os.environ.get("GEMINI_API_KEY")),
        "anthropic": lambda: bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": lambda: bool(os.environ.get("OPENAI_API_KEY")),
        "ollama": lambda: bool(os.environ.get("OLLAMA_URL")),
        "agy": lambda: bool(shutil.which("agy")),
    }
    modelos = {
        "openrouter": lambda: os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash"),
        "gemini": lambda: os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        "anthropic": lambda: os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        "openai": lambda: os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "ollama": lambda: os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        "agy": lambda: "agy-cli",
    }

    # 1. Si se pide un rol específico y tiene configuración propia en la matriz multi-agente (.ai-config.json)
    role_cfg = (cfg.get("roles") or {}).get(rol, {}) if (rol and isinstance(cfg.get("roles"), dict)) else {}
    r_prov = (role_cfg.get("provider") or "").strip().lower()
    r_model = (role_cfg.get("model") or "").strip()

    if r_prov in candidatos and candidatos[r_prov]():
        return {"provider": r_prov, "model": r_model or modelos[r_prov](), "rol": rol}

    # 1b. Si no hay override en .ai-config.json, leer default por rol desde agentes.yaml
    if not r_prov and not r_model:
        y_prov, y_model = _leer_api_desde_agentes_yaml(rol)
        if y_prov in candidatos and candidatos[y_prov]():
            return {"provider": y_prov, "model": y_model or modelos[y_prov](), "rol": rol}

    # 2. Proveedor preferido global (pero respetando modelo override del rol si se definió)
    preferido = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if preferido in candidatos and candidatos[preferido]():
        return {"provider": preferido, "model": r_model or modelos[preferido](), "rol": rol}

    for name in ("openrouter", "gemini", "anthropic", "openai", "ollama", "agy"):
        if candidatos[name]():
            return {"provider": name, "model": r_model or modelos[name](), "rol": rol}
    return {"provider": "heuristica", "model": "sin-api-key", "rol": rol}


def probar_conexion_ia(rol: str | None = None) -> dict:
    """Envía un prompt mínimo para verificar que la API key / modelo del rol responde."""
    import time

    prov = detectar_proveedor_ia(rol=rol)
    if prov["provider"] == "heuristica":
        return {
            "ok": False,
            "provider": "heuristica",
            "model": "sin-api-key",
            "rol": rol,
            "error": "No hay ninguna API key ni CLI configurado. Agrega tu OPENROUTER_API_KEY o GEMINI_API_KEY arriba.",
        }
    t0 = time.time()
    prompt = 'Responde únicamente con este JSON exacto: {"status": "ok"}'
    try:
        if prov["provider"] == "openrouter":
            res = _llamar_openrouter(prompt, prov["model"])
        elif prov["provider"] == "gemini":
            res = _llamar_gemini(prompt, prov["model"])
        elif prov["provider"] == "anthropic":
            res = _llamar_anthropic(prompt, prov["model"])
        elif prov["provider"] == "openai":
            res = _llamar_openai(prompt, prov["model"])
        elif prov["provider"] == "ollama":
            res = _llamar_ollama(prompt, prov["model"])
        elif prov["provider"] == "agy":
            res = _llamar_agy(prompt)
        else:
            raise ValueError("Proveedor no soportado")
        ms = int((time.time() - t0) * 1000)
        return {"ok": True, "provider": prov["provider"], "model": prov["model"], "rol": rol, "latency_ms": ms, "response": res}
    except Exception as exc:
        return {"ok": False, "provider": prov["provider"], "model": prov["model"], "rol": rol, "error": str(exc)}


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def consultar_grafo_contexto(
    texto_oferta: str,
    angulo: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """GraphRAG híbrido: consulta Neo4j (si está activo) o grafo_conocimiento.json
    combinando coincidencia de skills, similitud léxica/semántica y afinidad por ángulo."""
    texto_low = (texto_oferta or "").lower()
    neo_uri = os.environ.get("NEO4J_URI")

    if neo_uri:
        try:
            from neo4j import GraphDatabase

            auth_raw = os.environ.get("NEO4J_AUTH", "neo4j/career_neo4j_pass")
            user, pwd = auth_raw.split("/", 1) if "/" in auth_raw else ("neo4j", auth_raw)
            driver = GraphDatabase.driver(neo_uri, auth=(user, pwd))
            try:
                with driver.session() as session:
                    all_skills = [
                        r["nombre"]
                        for r in session.run("MATCH (s:Skill) WHERE s.nombre IS NOT NULL RETURN s.nombre AS nombre")
                    ]
                    matched = [
                        s for s in all_skills
                        if len(s) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", texto_low)
                    ]
                    if matched:
                        rows = session.run(
                            """
                            MATCH (l:Logro)-[:DEMUESTRA_SKILL]->(s:Skill)
                            WHERE toLower(s.nombre) IN $skills
                            RETURN l.id AS id, l.titulo AS titulo, l.ahorro AS ahorro,
                                   l.angulo AS angulo, collect(DISTINCT s.nombre) AS skills_match
                            ORDER BY size(skills_match) DESC
                            LIMIT $limit
                            """,
                            skills=[m.lower() for m in matched],
                            limit=top_k,
                        )
                        res_neo = [dict(r) for r in rows]
                        if res_neo:
                            return res_neo
            finally:
                driver.close()
        except Exception:
            pass

    # Fallback / modo principal local: GraphRAG híbrido sobre grafo_conocimiento.json
    if not GRAFO_JSON.exists():
        return []
    try:
        import grafo as _grafo_mod

        data = json.loads(GRAFO_JSON.read_text(encoding="utf-8"))
        nodos = data.get("nodos", {})
        logros = [n for n in nodos.values() if n.get("type") == "logro"]
        return _grafo_mod.buscar_logros_hibrido(logros, texto_oferta, angulo=angulo, top_k=top_k)
    except Exception:
        return []


def cargar_memoria_rechazos(max_ejemplos: int = 6) -> str:
    """Lee patrones reales de ofertas descartadas desde grafo_conocimiento.json para
    retroalimentar al evaluador LLM y evitar repetir los mismos mismatches."""
    if not GRAFO_JSON.exists():
        return "Sin historial de descartes indexado aún."
    try:
        data = json.loads(GRAFO_JSON.read_text(encoding="utf-8"))
        ap = data.get("aprendizaje_descartes") or {}
        if not ap:
            return "Sin historial de descartes indexado aún."

        lineas = [f"Total ofertas descartadas históricamente: {ap.get('total_descartadas', 0)}."]
        motivos = ap.get("motivos_frecuentes") or []
        if motivos:
            top_m = ", ".join(f"{m['cantidad']}x {m['motivo']}" for m in motivos[:6])
            lineas.append(f"- Motivos más frecuentes: {top_m}")

        sen_ej = ap.get("ejemplos_seniority") or []
        if sen_ej:
            ej_txt = "; ".join(f"{e['cargo']} ({e['empresa']})" for e in sen_ej[:max_ejemplos])
            lineas.append(f"- Roles ya descartados por EXCESO DE SENIORITY (NO volver a recomendar perfiles similares): {ej_txt}")

        perf_ej = ap.get("ejemplos_no_calza") or []
        if perf_ej:
            ej_txt = "; ".join(f"{e['cargo']} ({e['empresa']})" for e in perf_ej[:max_ejemplos])
            lineas.append(f"- Roles descartados por el usuario / no calzan con el perfil: {ej_txt}")

        toks = ap.get("tokens_frecuentes_en_descartadas") or []
        if toks:
            t_txt = ", ".join(f"{t['token']} ({t['cantidad']})" for t in toks[:8])
            lineas.append(f"- Términos repetidos en títulos descartados: {t_txt}")

        return "\n".join(lineas)
    except Exception:
        return "Sin historial de descartes indexado aún."


def _construir_prompt(oferta: dict, logros_grafo: list[dict]) -> str:
    preferencias = PREF_PATH.read_text(encoding="utf-8")[:5500] if PREF_PATH.exists() else ""
    perfil = PERFIL_PATH.read_text(encoding="utf-8")[:5000] if PERFIL_PATH.exists() else ""
    estructurado = _cargar_perfil_estructurado_safe()
    pref_json = estructurado.get("preferencias") or {}
    rub = pref_json.get("rubrica") or {}
    w_tec = int(rub.get("tecnico", 35))
    w_mod = int(rub.get("modalidad", 20))
    w_idi = int(rub.get("idioma", 15))
    w_sal = int(rub.get("salario", 15))
    w_sen = int(rub.get("seniority", 15))

    areas = pref_json.get("areas") or []
    angulos_permitidos = " | ".join([a["slug"] for a in areas if a.get("slug")] + ["base"]) if areas else "finops | devsecops | secops-siem | ai-automation | base"
    pais = pref_json.get("pais_residencia") or estructurado.get("datos_personales", {}).get("pais") or "Chile / LatAm"
    regiones = ", ".join(pref_json.get("regiones_aceptadas") or ["LatAm", "Global"])
    modalidad_pref = pref_json.get("modalidad") or "remoto_100"
    solo_ic = pref_json.get("solo_ic", True)
    seniority_obj = pref_json.get("seniority_objetivo") or "mid"
    exclusiones = ", ".join(pref_json.get("exclusiones") or [])

    grafo_txt = json.dumps(logros_grafo, ensure_ascii=False, indent=2) if logros_grafo else "[]"
    memoria_rechazos = cargar_memoria_rechazos()
    desc_limpia = (oferta.get("descripcion") or "")[:6500]

    return f"""Eres un evaluador estricto de ofertas laborales para el candidato descrito en el Perfil Maestro.
NO infles puntajes ni supongas que una oferta cumple la modalidad o geografía si no lo indica o si restringe residencia fuera del alcance del candidato.

=== PREFERENCIAS Y RÚBRICA OBLIGATORIA ===
{preferencias}

Resumen estructurado de filtros duros:
- País de residencia: {pais}
- Regiones/elegibilidad aceptadas: {regiones}
- Modalidad deseada: {modalidad_pref} (remoto_100 = exige 100% remoto; hibrido = acepta híbrido o remoto; presencial/cualquiera = flexible)
- Seniority objetivo: {seniority_obj}
- Solo Individual Contributor (IC, sin Manager/Director): {solo_ic}
- Exclusiones duras (no negociables): {exclusiones or 'Ninguna extra'}

=== PERFIL RESUMIDO DEL CANDIDATO ===
{perfil}

=== LOGROS VERIFICADOS RECUPERADOS DEL GRAFO (Neo4j GraphRAG) ===
{grafo_txt}

=== MEMORIA DE RECHAZO APRENDIDA DEL HISTORIAL DE FELIPE ===
{memoria_rechazos}

=== OFERTA A EVALUAR ===
Empresa: {oferta.get('empresa', '')}
Cargo: {oferta.get('titulo', '')}
Portal: {oferta.get('portal', '')}
Ubicación reportada: {oferta.get('ubicacion', '')}
Workplace type: {oferta.get('workplace_type', '')}
URL: {oferta.get('url', '')}
Descripción:
{desc_limpia}

INSTRUCCIONES DE SALIDA:
Responde ÚNICAMENTE con un objeto JSON válido (sin markdown ni texto extra) con exactamente estas claves:
{{
  "elegible_chile_remoto": true/false,
  "modalidad_real": "string corta, ej: 100% Remoto (LatAm / Global) o Remoto solo EE.UU. o Híbrido (Ciudad)",
  "es_ic_tecnico": true/false,
  "seniority_detectado": "Junior | Mid-Level | Semi-Senior | Senior | Staff/Lead/Principal | Manager",
  "idioma": "Inglés | Español | Bilingüe",
  "salario_detectado": "banda salarial si aparece en texto o 'Sueldo por confirmar'",
  "angulo": "{angulos_permitidos}",
  "desglose": {{
    "tecnico_35": 0-{w_tec},
    "remoto_chile_20": 0-{w_mod},
    "ingles_15": 0-{w_idi},
    "salario_empresa_15": 0-{w_sal},
    "seniority_15": 0-{w_sen},
    "penalizacion_seniority": 0 a -25
  }},
  "match_score": 0-100,
  "prioridad": "alta | media | stretch | descartada",
  "motivo_descarte": null o "motivo claro si descartada (ej: Restringido a otro país, No cumple modalidad deseada, Exceso de Seniority, Violación de no negociable, Score <65)",
  "keywords": ["lista de 4-10 skills/keywords reales de la oferta"],
  "brechas": ["lista de brechas reales frente al perfil del candidato"],
  "logros_grafo_match": ["títulos exactos de los logros del grafo que calzan con esta oferta"],
  "resumen_ia": "2 frases concisas en español explicando exactamente por qué obtuvo este puntaje y si conviene postular."
}}

REGLAS CRÍTICAS:
1. Evalúa `elegible_chile_remoto` como TRUE si la oferta es 100% remota y acepta candidatos en {pais} / {regiones} (incluyendo "Worldwide", "Anywhere", "Global", "Americas", "LatAm" o remoto sin restricción de país). Si exige residencia fiscal o permiso de trabajo exclusivo en EE.UU. (US-only), Canadá, Reino Unido, UE (sin aceptar contractor global) o presencialidad/hibridez -> elegible_chile_remoto=false, remoto_chile_20=0, prioridad="descartada", match_score <= 35.
2. Si `solo_ic` es true y el TÍTULO del cargo es Manager, Director, VP, Head of, Scrum Master o Project Manager -> es_ic_tecnico=false, prioridad="descartada", match_score <= 25. (NO descartes un rol IC solo porque la descripción mencione colaborar con un manager/lead o trabajar en una consultora de staff augmentation).
3. CALIBRACIÓN DE IDIOMA (`ingles_15`, máx {w_idi}): El candidato es nativo en Español + Traductor profesional Inglés C1. Si la oferta es en Inglés o exige Inglés avanzado -> otorga 13 a {w_idi} pts. Si la oferta está en Español (Chile/LatAm) -> NO castigues el puntaje: otorga 11 a 13 pts (el candidato cumple 100% el idioma y aporta inglés C1 como plus).
4. CALIBRACIÓN ESTRICTA DE SENIORITY (`seniority_15` y `penalizacion_seniority` — MÁXIMA PRIORIDAD ANTI-MISMATCH):
   - Felipe tiene ~3 años de experiencia técnica real (perfil Junior Avanzado / Mid-Level). Su principal causa de mismatch son ofertas que piden demasiados años o seniority Senior duro.
   - Si pide 1 a 3 años (Junior, Mid, Semi-Senior, Analyst, Specialist): `seniority_15` = 13 a {w_sen}, `penalizacion_seniority` = 0.
   - Si exige **5+ años**, **4-6+ años**, título **Staff / Principal / Lead / Architect**, o dice **Senior / Sr.** en el título sin ser FinOps AWS puro (>=85% match): aplica `penalizacion_seniority` = -20 a -25, `seniority_15` = 0 a 3, `prioridad: "descartada"` y `motivo_descarte: "Exceso de seniority / experiencia requerida"`.
   - Solo clasifica como `prioridad: "stretch"` (score 60-74, nunca `alta`) si pide 3–4 años (o Senior) pero es un rol FinOps AWS / SecOps Wazuh con >=80% de coincidencia exacta con su stack actual.
5. MEMORIA DE RECHAZO (AI EVALUATION): Si el rol es de AI Evaluator / AI Trainer pero pertenece a disciplinas científicas/académicas/creativas no informáticas (ej: Life Sciences, Physical Sciences, Mathematics, Biology, Chemistry, Physics, Medicine, Humanities, Arts, Consumer/Lifestyle, acentos regionales de inglés británico/australiano/canadiense o idiomas distintos a Español/Inglés), márcala OBLIGATORIAMENTE como prioridad="descartada", match_score <= 30 y motivo_descarte="Disciplina o variante lingüística ajena al perfil".
6. `match_score` debe ser exactamente la suma coherente de `tecnico_35 + remoto_chile_20 + ingles_15 + salario_empresa_15 + seniority_15 + penalizacion_seniority` (clamp 0..100). Prioridad: `alta` (>=80), `media` (65-79 sin brecha fuerte de seniority), `stretch` (60-79 cuando es desafiante por años/seniority pero buen match técnico), `descartada` (<60 o filtro duro incumplido).
"""


def _extraer_json(texto: str) -> dict:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)
    m = re.search(r"\{.*\}", texto, flags=re.S)
    if not m:
        raise ValueError("No se encontró objeto JSON en la respuesta del LLM")
    return json.loads(m.group(0))


def _llamar_openrouter(prompt: str, model: str) -> dict:
    api_key = os.environ["OPENROUTER_API_KEY"]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "Career Agent",
    }
    base_payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    }
    # Algunos modelos en OpenRouter soportan response_format json_object y otros (ej. ciertos :free) no.
    for use_rf in (True, False):
        payload = dict(base_payload)
        if use_rf:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return _extraer_json(data["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as err:
            if use_rf and err.code == 400:
                continue
            detail = err.read().decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(f"HTTP {err.code}: {detail}") from err
    raise RuntimeError("No se pudo obtener respuesta JSON de OpenRouter")


def _llamar_gemini(prompt: str, model: str) -> dict:
    api_key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _extraer_json(text)


def _llamar_anthropic(prompt: str, model: str) -> dict:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return _extraer_json(text)


def _llamar_openai(prompt: str, model: str) -> dict:
    api_key = os.environ["OPENAI_API_KEY"]
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _extraer_json(data["choices"][0]["message"]["content"])


def _llamar_ollama(prompt: str, model: str) -> dict:
    base = os.environ["OLLAMA_URL"].rstrip("/")
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _extraer_json(data["message"]["content"])


def _llamar_agy(prompt: str) -> dict:
    res = subprocess.run(
        [
            "agy",
            "--disable-slash-commands",
            "--effort", "low",
            "--output-format", "text",
            "-p", prompt,
        ],
        text=True,
        capture_output=True,
        timeout=90,
        check=True,
    )
    return _extraer_json(res.stdout)


def llamar_llm_json(prompt: str, rol: str | None = None) -> dict:
    """Invoca al proveedor de IA configurado para el rol indicado y devuelve un JSON parseado."""
    prov = detectar_proveedor_ia(rol=rol)
    if prov["provider"] == "heuristica":
        raise RuntimeError("No hay proveedor de IA configurado. Agrega una API Key en 'API Keys & IA'.")
    if prov["provider"] == "openrouter":
        return _llamar_openrouter(prompt, prov["model"])
    if prov["provider"] == "gemini":
        return _llamar_gemini(prompt, prov["model"])
    if prov["provider"] == "anthropic":
        return _llamar_anthropic(prompt, prov["model"])
    if prov["provider"] == "openai":
        return _llamar_openai(prompt, prov["model"])
    if prov["provider"] == "ollama":
        return _llamar_ollama(prompt, prov["model"])
    if prov["provider"] == "agy":
        return _llamar_agy(prompt)
    raise ValueError(f"Proveedor no soportado: {prov['provider']}")


def evaluar_heuristica(oferta: dict, logros_grafo: list[dict]) -> dict:
    """Evaluador determinista honesto (cuando no hay API key de IA configurada).
    Usa el perfil estructurado del usuario (áreas, skills, modalidad, país, seniority y exclusiones)."""
    titulo = (oferta.get("titulo") or "").lower()
    ubic = (oferta.get("ubicacion") or "").lower()
    wp = (oferta.get("workplace_type") or "").lower()
    desc = (oferta.get("descripcion") or "").lower()
    full = f"{titulo} {ubic} {desc}"

    estructurado = _cargar_perfil_estructurado_safe()
    pref = estructurado.get("preferencias") or {}
    rub = pref.get("rubrica") or {}
    w_tec = int(rub.get("tecnico", 35))
    w_mod = int(rub.get("modalidad", 20))
    w_idi = int(rub.get("idioma", 15))
    w_sal = int(rub.get("salario", 15))
    w_sen = int(rub.get("seniority", 15))

    solo_ic = pref.get("solo_ic", True)
    modalidad_pref = pref.get("modalidad") or "remoto_100"
    pais = (pref.get("pais_residencia") or estructurado.get("datos_personales", {}).get("pais") or "Chile").lower()
    regiones = [r.lower() for r in (pref.get("regiones_aceptadas") or ["chile", "latam", "latin america", "worldwide", "anywhere", "global", "americas", "remote", "remoto"])]
    if pais and pais not in regiones:
        regiones.append(pais)

    # 1. Filtro IC técnico
    es_manager = solo_ic and any(k in titulo for k in ["manager", "director", "head of", "vp ", "scrum master", "project manager"])

    # 2. Modalidad y elegibilidad geográfica
    es_hibrido_presencial = (
        wp in ("hybrid", "onsite", "on-site")
        or bool(re.search(r"\b(hybrid|on-site|onsite|in-office|presencial|híbrido)\b", f"{ubic} {wp}"))
    )
    solo_otro_pais = bool(
        re.search(
            r"\b(us only|usa only|u\.s\. only|united states only|must reside in the u\.?s|authorized to work in the u\.?s|"
            r"eu only|europe only|uk only|united kingdom only|canada only|emea only|apac only|india only|"
            r"germany only|spain only|france only|australia only|us-based|based in the united states|residents of the us)\b",
            f"{ubic} {desc[:2500]}",
        )
    )
    # Si el usuario reside en EE.UU./EU/UK, no penalizar esa restricción
    if pais in ("usa", "us", "united states", "estados unidos") and "us" in f"{ubic} {desc[:1200]}":
        solo_otro_pais = False
    if pais in ("españa", "spain", "alemania", "germany", "francia", "france") and any(k in f"{ubic} {desc[:1200]}" for k in ("eu", "europe", "spain", "españa")):
        solo_otro_pais = False

    permite_region = any(re.search(r"\b" + re.escape(r) + r"\b", f"{ubic} {desc[:2000]}") for r in regiones if len(r) >= 2)
    if modalidad_pref == "remoto_100":
        elegible = (not es_hibrido_presencial) and (not solo_otro_pais) and permite_region
    elif modalidad_pref == "hibrido":
        elegible = (not solo_otro_pais) and (permite_region or pais in ubic)
    else:
        elegible = not solo_otro_pais

    if es_hibrido_presencial and modalidad_pref == "remoto_100":
        modalidad_real = f"Híbrido/Presencial ({oferta.get('ubicacion') or wp})"
    elif solo_otro_pais:
        modalidad_real = f"Restringido fuera de tu región ({oferta.get('ubicacion') or 'Otro país'})"
    elif elegible:
        modalidad_real = f"Compatible ({oferta.get('ubicacion') or 'Remoto / Global'})"
    else:
        modalidad_real = f"Ubicación no verificada ({oferta.get('ubicacion') or 'Sin especificar'})"

    # 3. Puntaje Técnico Core dinámico usando áreas y skills del usuario
    areas = pref.get("areas") or []
    skills_user = [
        s.lower()
        for lvl in ("avanzado", "intermedio")
        for s in (estructurado.get("skills", {}).get(lvl) or [])
        if isinstance(s, str) and len(s.strip()) >= 2
    ]

    angulo = "base"
    matched_skills_user: list[str] = []
    if areas or skills_user:
        best_area_hits = 0
        for a in areas:
            kws = [str(k).lower() for k in (a.get("keywords") or []) if str(k).strip()]
            hits = sum(1 for k in kws if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", full))
            if hits > best_area_hits:
                best_area_hits = hits
                angulo = a.get("slug") or "base"
        matched_skills_user = [
            s for s in skills_user
            if re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", full)
        ]
        ratio = min(1.0, (len(set(matched_skills_user)) + best_area_hits * 1.5) / 6.0)
        tecnico = int(round(w_tec * max(0.2, ratio)))
    else:
        # Fallback clásico si aún no existe perfil-estructurado.json
        tiene_finops = bool(re.search(r"\b(finops|cloud cost|cost optimization|savings plans|cur|cost explorer|cloud financial)\b", full))
        tiene_aws = bool(re.search(r"\b(aws|amazon web services|iam|guardduty|cloudwatch|ec2|s3|lambda|boto3)\b", full))
        tiene_secops = bool(re.search(r"\b(devsecops|secops|siem|wazuh|cloud security|incident response|guardduty)\b", full))
        tiene_ai_eval = bool(re.search(r"\b(ai trainer|ai tutor|ai evaluator|ai rater|rlhf|prompt evaluation|model evaluation|rubric|data annotation)\b", full))
        tiene_ai = bool(re.search(r"\b(ai automation|llm|openai|claude|gemini|python|automation|n8n|workflows?)\b", full))
        if tiene_finops and tiene_aws:
            tecnico, angulo = w_tec, "finops"
        elif tiene_finops:
            tecnico, angulo = int(w_tec * 0.85), "finops"
        elif tiene_secops and tiene_aws:
            tecnico, angulo = int(w_tec * 0.8), "secops-siem"
        elif tiene_ai_eval:
            tecnico, angulo = int(w_tec * 0.8), "ai-evaluator"
        elif tiene_aws and tiene_ai:
            tecnico, angulo = int(w_tec * 0.74), "ai-automation"
        elif tiene_aws:
            tecnico, angulo = int(w_tec * 0.63), "devsecops"
        elif tiene_ai:
            tecnico, angulo = int(w_tec * 0.45), "ai-automation"
        else:
            tecnico, angulo = int(w_tec * 0.28), "base"

    # 4. Modalidad / Ubicación
    remoto_pts = w_mod if elegible else 0

    # 5. Idioma
    es_ingles = bool(re.search(r"\b(english|responsibilities|requirements|qualifications|benefits|apply)\b", full))
    idioma = "Inglés" if es_ingles else "Español"
    idiomas_user = " ".join(pref.get("idiomas_trabajo") or ["español", "inglés"]).lower()
    if es_ingles and ("ingl" in idiomas_user or "english" in idiomas_user):
        ingles_pts = w_idi
    elif not es_ingles and ("españ" in idiomas_user or "span" in idiomas_user):
        ingles_pts = w_idi
    else:
        ingles_pts = int(w_idi * 0.65)

    # 6. Salario / Empresa
    m_sal = re.search(
        r"(\$\s?\d{1,3}(?:,\d{3})+(?:\s*[-–to]+\s*\$?\s?\d{1,3}(?:,\d{3})+)?(?:\s*(?:USD|CLP|EUR|/yr|/year|/mo|/month))?)",
        oferta.get("descripcion") or "",
        re.I,
    )
    salario_txt = m_sal.group(1) if m_sal else (oferta.get("salario") or "Sueldo por confirmar")
    salario_pts = int(w_sal * 0.8) if (es_ingles or m_sal) else int(w_sal * 0.6)

    # 7. Seniority respecto al seniority objetivo del usuario
    sen_obj = (pref.get("seniority_objetivo") or "mid").lower()
    re_5_plus = re.compile(
        r"\b(?:[5-9]|1[0-9])\s*\+?\s*(?:years?|yrs?|años)\b|"
        r"\b(?:at least|minimum(?: of)?|mínimo(?: de)?)\s+(?:[5-9]|1[0-9])\s*(?:years?|yrs?|años)\b|"
        r"\b[34]\s*[-–to]+\s*(?:[6-9]|1[0-9])\s*(?:years?|yrs?|años)\b",
        re.I,
    )
    es_exceso = sen_obj in ("junior", "mid") and (
        any(k in titulo for k in ["staff", "principal", "architect", "lead"])
        or bool(re_5_plus.search(desc))
    )
    es_senior = "senior" in titulo or "sr." in titulo or bool(re.search(r"\b4\+?\s*(?:years?|años)\b", desc))
    if es_manager:
        seniority_det = "Manager"
        seniority_pts, pen = 0, -25
    elif es_exceso:
        seniority_det = "Staff/Lead/Principal"
        seniority_pts, pen = 0, -25
    elif es_senior and sen_obj in ("junior", "mid"):
        seniority_det = "Senior"
        seniority_pts, pen = int(w_sen * 0.35), -10
    else:
        seniority_det = "Senior" if es_senior else "Mid-Level"
        seniority_pts, pen = w_sen, 0

    # 8. Exclusiones duras explícitas (términos de rol/seniority SOLO en título; dominios en título)
    TERMS_SOLO_TITULO = {
        "presencial", "híbrido", "hibrido", "us only", "eu only",
        "manager", "director", "scrum master", "project manager",
        "staff", "principal", "lead", "vp", "head of", "chief",
    }
    excl_hit = None
    for ex in pref.get("exclusiones") or []:
        ex_s = str(ex).strip().lower()
        if len(ex_s) < 3 or ex_s in ("presencial", "híbrido", "hibrido", "us only", "eu only"):
            continue
        target_txt = titulo if ex_s in TERMS_SOLO_TITULO else f"{titulo} {desc[:600]}"
        if re.search(r"\b" + re.escape(ex_s) + r"\b", target_txt):
            excl_hit = ex_s
            break

    raw_score = max(0, min(100, tecnico + remoto_pts + ingles_pts + salario_pts + seniority_pts + pen))
    motivo = None
    if es_manager:
        raw_score = min(raw_score, 25)
        prioridad = "descartada"
        motivo = "Rol de gestión/Manager (tu perfil busca IC técnico)"
    elif excl_hit:
        raw_score = min(raw_score, 30)
        prioridad = "descartada"
        motivo = f"Coincide con exclusión no negociable: '{excl_hit}'"
    elif not elegible:
        raw_score = min(raw_score, 35)
        prioridad = "descartada"
        motivo = f"No cumple tu modalidad/geografía ({modalidad_real})"
    elif es_exceso:
        prioridad = "descartada"
        motivo = "Exceso de seniority frente a tu objetivo"
    elif raw_score >= 80 and not (es_senior and sen_obj in ("junior", "mid")):
        prioridad = "alta"
    elif es_senior and sen_obj in ("junior", "mid") and raw_score >= 60:
        raw_score = min(raw_score, 74)
        prioridad = "stretch"
    elif raw_score >= 65:
        prioridad = "media"
    else:
        prioridad = "descartada"
        motivo = f"Score insuficiente ({raw_score}/100) según rúbrica"

    brechas = []
    if es_senior and sen_obj in ("junior", "mid"):
        brechas.append("Pide perfil Senior / más años de experiencia")
    if not matched_skills_user and not logros_grafo:
        brechas.append("Poca superposición directa con tus skills declarados")

    return {
        "elegible_chile_remoto": elegible,
        "modalidad_real": modalidad_real,
        "es_ic_tecnico": not es_manager,
        "seniority_detectado": seniority_det,
        "idioma": idioma,
        "salario_detectado": salario_txt,
        "angulo": angulo,
        "desglose": {
            "tecnico_35": tecnico,
            "remoto_chile_20": remoto_pts,
            "ingles_15": ingles_pts,
            "salario_empresa_15": salario_pts,
            "seniority_15": seniority_pts,
            "penalizacion_seniority": pen,
        },
        "match_score": raw_score,
        "prioridad": prioridad,
        "motivo_descarte": motivo,
        "keywords": (oferta.get("keywords_matched") or matched_skills_user)[:10],
        "brechas": brechas[:5] or ["Verificar requisitos en detalle"],
        "logros_grafo_match": [l["titulo"] for l in logros_grafo[:4]],
        "resumen_ia": (
            f"Evaluación heurística (sin API key): {raw_score}/100. "
            f"{'Cumple modalidad/geografía.' if elegible else 'Descartada por modalidad/ubicación.'} "
            f"Configura una API Key en 'API Keys & IA' para evaluación semántica completa."
        ),
        "evaluado_por": "heuristica (sin API key)",
    }


def evaluar_oferta(oferta: dict, rol: str = "buscador-ofertas") -> dict:
    """Evalúa una oferta usando Neo4j GraphRAG + LLM configurado para el rol (o heurística fallback)."""
    texto_completo = f"{oferta.get('titulo', '')} {oferta.get('ubicacion', '')} {oferta.get('descripcion', '')}"
    logros_grafo = consultar_grafo_contexto(texto_completo)
    prov = detectar_proveedor_ia(rol=rol)

    if prov["provider"] == "heuristica":
        return evaluar_heuristica(oferta, logros_grafo)

    prompt = _construir_prompt(oferta, logros_grafo)
    try:
        if prov["provider"] == "openrouter":
            res = _llamar_openrouter(prompt, prov["model"])
        elif prov["provider"] == "gemini":
            res = _llamar_gemini(prompt, prov["model"])
        elif prov["provider"] == "anthropic":
            res = _llamar_anthropic(prompt, prov["model"])
        elif prov["provider"] == "openai":
            res = _llamar_openai(prompt, prov["model"])
        elif prov["provider"] == "ollama":
            res = _llamar_ollama(prompt, prov["model"])
        elif prov["provider"] == "agy":
            res = _llamar_agy(prompt)
        else:
            return evaluar_heuristica(oferta, logros_grafo)

        res["evaluado_por"] = f"{prov['provider']}/{prov['model']} + GraphRAG"
        if not res.get("logros_grafo_match") and logros_grafo:
            res["logros_grafo_match"] = [l["titulo"] for l in logros_grafo[:4]]
        # Asegurar coherencia de tipos, desglose y rangos de estado.py validar
        res["match_score"] = max(0, min(100, int(res.get("match_score", 0))))
        prio_raw = str(res.get("prioridad") or "media").strip().lower()
        if prio_raw not in ("alta", "media", "stretch", "descartada"):
            prio_raw = "media"
        res["prioridad"] = prio_raw

        if not res.get("elegible_chile_remoto", False) or not res.get("es_ic_tecnico", True):
            res["prioridad"] = "descartada"
            res["match_score"] = min(res["match_score"], 35)
            if not res.get("motivo_descarte"):
                res["motivo_descarte"] = "No cumple filtros duros (remoto Chile/LatAm o rol IC)"
        elif res["prioridad"] == "descartada":
            if not res.get("motivo_descarte"):
                res["motivo_descarte"] = f"Descartada según rúbrica ({res['match_score']}/100)"
        elif res["prioridad"] == "stretch":
            if res["match_score"] < 60:
                res["prioridad"] = "descartada"
                res["motivo_descarte"] = res.get("motivo_descarte") or f"Stretch por debajo del umbral ({res['match_score']}/100)"
        elif res["match_score"] < 65:
            # Si obtuvo 61-64 por leve brecha de años pero el resumen recomienda postular/stretch, preservar como stretch
            resumen_low = str(res.get("resumen_ia") or "").lower()
            if res["match_score"] >= 61 and ("stretch" in resumen_low or "conviene postular" in resumen_low or "interesante para postular" in resumen_low):
                res["prioridad"] = "stretch"
                res["motivo_descarte"] = None
            else:
                res["prioridad"] = "descartada"
                if not res.get("motivo_descarte"):
                    res["motivo_descarte"] = f"Puntaje inferior al mínimo ({res['match_score']}/100)"
        else:
            # Coherencia exacta con estado.py validar: alta >= 80, media 65..79
            if res["match_score"] >= 80 and res["prioridad"] == "media":
                res["prioridad"] = "alta"
            elif res["match_score"] < 80 and res["prioridad"] == "alta":
                res["prioridad"] = "media"
            res["motivo_descarte"] = None
        return res
    except Exception as exc:
        fb = evaluar_heuristica(oferta, logros_grafo)
        fb["resumen_ia"] = f"Fallback heurístico (error en {prov['provider']}: {exc}). " + fb["resumen_ia"]
        return fb


def triaje_lote_ofertas(candidatas: list[dict], max_seleccion: int = 20, rol: str = "buscador-ofertas") -> dict:
    """Triaje semántico rápido en lote (1 llamada JSON barata para 30–50 ofertas) ANTES
    de gastar múltiples llamadas profundas con GraphRAG.
    Permite que la IA piense en el momento correcto:
      - Detecta ofertas disfrazadas (US-only/EU-only en descripción, 5+ años duros, dominios ajenos).
      - Prioriza las joyas reales para Felipe (FinOps, AWS, DevSecOps, SecOps/Wazuh, AI Automation, AI Eval bilingüe/código).
    Devuelve: {"seleccionadas": [indices...], "descartadas": [{"idx": i, "motivo": "..."}]}
    """
    if not candidatas:
        return {"seleccionadas": [], "descartadas": []}
    prov = detectar_proveedor_ia(rol=rol)
    if prov["provider"] == "heuristica" or len(candidatas) <= max_seleccion:
        return {"seleccionadas": list(range(min(len(candidatas), max_seleccion))), "descartadas": []}

    estructurado = _cargar_perfil_estructurado_safe()
    pref = estructurado.get("preferencias") or {}
    pais = pref.get("pais_residencia") or "Chile"
    regiones = ", ".join(pref.get("regiones_aceptadas") or ["Chile", "LatAm", "Global", "Worldwide"])

    items_compactos = []
    for idx, c in enumerate(candidatas[:55]):
        desc_snip = re.sub(r"\s+", " ", (c.get("descripcion") or ""))[:340]
        items_compactos.append({
            "idx": idx,
            "empresa": c.get("empresa", ""),
            "titulo": c.get("titulo", ""),
            "ubicacion": c.get("ubicacion", ""),
            "portal": c.get("portal", ""),
            "extracto": desc_snip,
        })

    memoria_rechazos = cargar_memoria_rechazos(max_ejemplos=5)

    prompt = f"""Eres el reclutador técnico principal de Felipe Vergara Peñaloza (reside en {pais}; busca 100% Remoto elegible desde {regiones}).
Perfil de Felipe:
- Experiencia real: ~3 años (Junior Avanzado / Mid-Level). NO es Senior de 5+ años ni Staff/Lead.
- Rol objetivo #1 (máxima prioridad): Cloud FinOps Engineer / FinOps Analyst / AWS Cost Optimization.
- Rol objetivo #2: DevSecOps / AWS Cloud Operations / Cloud Security / IAM / SecOps / Wazuh SIEM (nivel Junior, Mid, Semi-Senior o Analyst de 1–3 años; evitar roles Senior genéricos de 5+ años).
- Rol objetivo #3: AI Workflow Automation Engineer (Python, Boto3, LLMs, RAG, n8n).
- Rol secundario (ingreso extra USD/h): AI Evaluator / RLHF SOLO en Español LatAm/Chile, Bilingüe EN-ES general, Python, DevOps, Cloud o Ciberseguridad. (DESCARTAR acentos de inglés británico/australiano/canadiense/NZ, otros idiomas, ciencias biológicas/físicas/médicas, humanidades, arte o negocios generales).

MEMORIA DE RECHAZO HISTÓRICA (evita estos mismos errores):
{memoria_rechazos}

Revisa esta lista de {len(items_compactos)} vacantes candidatas y:
1. Pon en `seleccionadas` hasta {max_seleccion} índices (`idx`) ordenados de MEJOR a MENOR calce real para pasar a evaluación profunda. Prioriza siempre FinOps, AWS, DevSecOps, SecOps (1-3 años / Mid) y empresas de Chile/LatAm/Global remoto real.
2. Pon en `descartadas` los `idx` que claramente NO sirven (restringidas a EE.UU./Canadá/UE/Reino Unido/Asia, híbridas/presenciales, roles Senior/Staff/Principal/Lead/Manager o que exijan 5+ años de experiencia, stack 100% ajeno, o AI Evaluator de disciplinas/idiomas ajenos). Los `idx` dudosos que simplemente no cupieron en el top {max_seleccion} NO los pongas en `descartadas`.

CANDIDATAS:
{json.dumps(items_compactos, ensure_ascii=False)}

Responde ÚNICAMENTE con JSON válido:
{{
  "seleccionadas": [lista de enteros idx, máx {max_seleccion}],
  "descartadas": [{{"idx": entero, "motivo": "string breve"}}]
}}"""
    try:
        res = llamar_llm_json(prompt, rol=rol)
        sel = [int(i) for i in (res.get("seleccionadas") or []) if isinstance(i, int) and 0 <= i < len(candidatas)]
        desc_out = []
        sel_set = set(sel)
        for d in (res.get("descartadas") or []):
            if isinstance(d, dict) and isinstance(d.get("idx"), int):
                i = d["idx"]
                if 0 <= i < len(candidatas) and i not in sel_set:
                    desc_out.append({"idx": i, "motivo": str(d.get("motivo") or "Descartada en triaje IA")[:120]})
        # Si seleccionó menos de max_seleccion, rellenar con las mejores por pre_score no descartadas
        desc_ids = {d["idx"] for d in desc_out}
        if len(sel) < max_seleccion:
            for i in range(len(candidatas)):
                if i not in sel_set and i not in desc_ids:
                    sel.append(i)
                    sel_set.add(i)
                    if len(sel) >= max_seleccion:
                        break
        return {"seleccionadas": sel[:max_seleccion], "descartadas": desc_out}
    except Exception:
        return {"seleccionadas": list(range(min(len(candidatas), max_seleccion))), "descartadas": []}


def descargar_oferta_url(url: str) -> dict:
    """Descarga una página de oferta laboral y extrae título, empresa, ubicación y texto limpio
    usando JSON-LD JobPosting / APIs conocidas antes de caer en heurística de <title>."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    empresa = ""
    titulo = ""
    ubicacion = "Ver descripción"
    portal = "URL Analizada"
    desc_clean = ""

    # 1. Intentar extraer Schema.org JobPosting desde <script type="application/ld+json">
    for m_ld in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw, flags=re.I | re.S):
        try:
            ld = json.loads(m_ld.group(1).strip())
            nodes = ld if isinstance(ld, list) else (ld.get("@graph") if isinstance(ld, dict) and "@graph" in ld else [ld])
            for node in nodes or []:
                if isinstance(node, dict) and node.get("@type") == "JobPosting":
                    titulo = strip_html(str(node.get("title") or "")).strip()
                    org = node.get("hiringOrganization")
                    if isinstance(org, dict):
                        empresa = strip_html(str(org.get("name") or "")).strip()
                    elif isinstance(org, str):
                        empresa = org.strip()
                    if node.get("description"):
                        desc_clean = strip_html(str(node.get("description")))
                    if str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE":
                        ubicacion = "100% Remoto"
                    break
            if titulo and empresa:
                break
        except Exception:
            continue

    # 2. Fallback sobre <title> limpiando prefijos tipo "Remote (work from home) — Cloud Engineer at Alegra | Get on Board"
    if not titulo or not empresa:
        m_title = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
        title_raw = strip_html(m_title.group(1)) if m_title else "Oferta Web"
        # Limpiar sufijos de portales conocidos
        for pname, pat in [
            ("Get on Board", r"\s*[|\-–—]\s*Get on Board.*$"),
            ("LinkedIn", r"\s*[|\-–—]\s*LinkedIn.*$"),
            ("FinOps Jobs", r"\s*[|\-–—]\s*FinOps Jobs.*$"),
        ]:
            if re.search(pat, title_raw, re.I):
                portal = pname
                title_raw = re.sub(pat, "", title_raw, flags=re.I).strip()
        # Quitar prefijo de modalidad de Get on Board ("Remote (work from home) - " o "Remoto - ")
        title_raw = re.sub(r"^(?:remote\s*\([^)]*\)|remoto(?:\s*\([^)]*\))?)\s*[-|–—:]\s*", "", title_raw, flags=re.I).strip()
        m_at = re.search(r"^(.+?)\s+(?:at|en|@)\s+([^|\-–—]+)$", title_raw, flags=re.I)
        if m_at:
            titulo = titulo or m_at.group(1).strip()
            empresa = empresa or m_at.group(2).strip()
        else:
            parts = [p.strip() for p in re.split(r"\s+[-|–—]\s+", title_raw) if p.strip()]
            # Filtrar fragmentos que sean "Remote..."
            parts_clean = [p for p in parts if not re.match(r"^(remote|remoto|work from home|full[- ]time)\b", p, re.I)] or parts
            titulo = titulo or (parts_clean[0] if parts_clean else title_raw)
            if not empresa:
                empresa = parts_clean[1] if len(parts_clean) > 1 else urllib.request.urlparse(url).netloc.replace("www.", "").split(".")[0].capitalize()

    if "getonbrd.com" in url.lower():
        portal = "Get on Board"

    return {
        "empresa": empresa or "Empresa",
        "titulo": titulo or "Oferta Web",
        "url": url,
        "portal": portal,
        "ubicacion": ubicacion,
        "workplace_type": "remote" if "remoto" in ubicacion.lower() or "remote" in raw[:3000].lower() else "",
        "descripcion": (desc_clean or strip_html(raw))[:10000],
    }


def guardar_ficha_evaluada(oferta: dict, ev: dict, hoy: str | None = None) -> tuple[Path | None, str]:
    hoy = hoy or dt.date.today().strftime("%Y-%m-%d")
    slug_emp = re.sub(r"[^a-z0-9]+", "-", (oferta.get("empresa") or "empresa").lower()).strip("-")
    slug_rol = re.sub(r"[^a-z0-9]+", "-", (oferta.get("titulo") or "rol").lower()).strip("-")
    file_slug = f"{slug_emp}-{slug_rol}"[:60].rstrip("-")
    oid = f"{hoy}--{file_slug}"

    res_ex = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "estado.py"), "existe", oferta["url"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if res_ex.returncode == 0:
        return None, "ya existe"

    dir_hoy = ROOT / "ofertas" / hoy
    dir_hoy.mkdir(parents=True, exist_ok=True)
    file_path = dir_hoy / f"{file_slug}.md"

    estado = "descartada" if ev.get("prioridad") == "descartada" else "evaluada"
    frontmatter = {
        "tipo": "oferta",
        "id": oid,
        "legacy_id": None,
        "empresa": oferta.get("empresa") or "Empresa",
        "cargo": oferta.get("titulo") or "Rol",
        "url": oferta["url"],
        "portal": oferta.get("portal") or "Web",
        "fecha_captura": hoy,
        "modalidad": ev.get("modalidad_real") or oferta.get("ubicacion") or "Por confirmar",
        "idioma": ev.get("idioma") or "Inglés (C1)",
        "salario": ev.get("salario_detectado") or "Sueldo por confirmar",
        "match_score": ev.get("match_score"),
        "prioridad": ev.get("prioridad") or "media",
        "estado": estado,
        "angulo": ev.get("angulo") or "devsecops",
        "cv": None,
        "motivo_descarte": ev.get("motivo_descarte") if estado == "descartada" else None,
        "keywords": ev.get("keywords") or [],
        "brechas": ev.get("brechas") or [],
        "desglose": ev.get("desglose") or {},
        "logros_grafo": ev.get("logros_grafo_match") or [],
        "resumen_ia": ev.get("resumen_ia") or "",
        "evaluado_por": ev.get("evaluado_por") or "",
        "notas": ev.get("resumen_ia") or "",
        "fecha_postulacion": None,
        "actualizado": hoy,
    }
    if oferta.get("fecha_publicacion"):
        frontmatter["fecha_publicacion"] = str(oferta["fecha_publicacion"])[:10]
    if "heuristica" in (ev.get("evaluado_por") or ""):
        frontmatter["revisar"] = "Evaluada sin API key (heurística) — configurar GEMINI_API_KEY en .env"

    d = ev.get("desglose") or {}
    body = f"""
# {frontmatter['empresa']} — {frontmatter['cargo']}

- **Fecha de captura:** {hoy}
- **URL:** [{frontmatter['portal']}]({frontmatter['url']})
- **Modalidad verificada:** {frontmatter['modalidad']}
- **Seniority detectado:** {ev.get('seniority_detectado', 'N/A')}
- **Idioma:** {frontmatter['idioma']}
- **Salario:** {frontmatter['salario']}
- **Match Score:** **{frontmatter['match_score']}/100** (`{frontmatter['prioridad']}`) — *{frontmatter['evaluado_por']}*

## Resumen de Evaluación
{ev.get('resumen_ia', '')}

## Desglose de Rúbrica
- Técnico Core (máx 35): **{d.get('tecnico_35', '-')}**
- Remoto Chile/LatAm (máx 20): **{d.get('remoto_chile_20', '-')}**
- Inglés C1 (máx 15): **{d.get('ingles_15', '-')}**
- Salario / Empresa (máx 15): **{d.get('salario_empresa_15', '-')}**
- Seniority 1–3 años (máx 15): **{d.get('seniority_15', '-')}** (penalización: {d.get('penalizacion_seniority', 0)})

## Logros del Grafo (Neo4j GraphRAG) que hacen match
{chr(10).join(f'- {l}' for l in (ev.get('logros_grafo_match') or ['Sin match directo en grafo']))}

## Brechas Detectadas
{chr(10).join(f'- {b}' for b in (ev.get('brechas') or ['Ninguna excluyente']))}
"""

    with file_path.open("w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.dump(frontmatter, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        f.write("---\n")
        f.write(body)

    return file_path, oid


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", help="Analizar una oferta desde su URL")
    p.add_argument("--guardar", action="store_true", help="Guardar ficha en ofertas/<hoy>/")
    p.add_argument("--status", action="store_true", help="Mostrar proveedor de IA activo")
    args = p.parse_args()

    if args.status:
        print(json.dumps(detectar_proveedor_ia(), ensure_ascii=False))
        return 0

    if not args.url:
        p.print_help()
        return 1

    oferta = descargar_oferta_url(args.url)
    ev = evaluar_oferta(oferta)
    out = {"oferta": {k: v for k, v in oferta.items() if k != "descripcion"}, "evaluacion": ev}
    if args.guardar:
        fpath, oid = guardar_ficha_evaluada(oferta, ev)
        out["guardado"] = {"path": str(fpath.relative_to(ROOT)) if fpath else None, "id": oid}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
