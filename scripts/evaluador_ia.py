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


def consultar_grafo_contexto(texto_oferta: str) -> list[dict]:
    """GraphRAG: consulta Neo4j (o JSON local fallback) para recuperar logros del
    Impact Log cuyos skills hacen match directo con el texto de la oferta."""
    texto_low = texto_oferta.lower()
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
                    if not matched:
                        return []
                    rows = session.run(
                        """
                        MATCH (l:Logro)-[:DEMUESTRA_SKILL]->(s:Skill)
                        WHERE toLower(s.nombre) IN $skills
                        RETURN l.id AS id, l.titulo AS titulo, l.ahorro AS ahorro,
                               l.angulo AS angulo, collect(DISTINCT s.nombre) AS skills_match
                        ORDER BY size(skills_match) DESC
                        LIMIT 5
                        """,
                        skills=[m.lower() for m in matched],
                    )
                    return [dict(r) for r in rows]
            finally:
                driver.close()
        except Exception:
            pass

    # Fallback sobre grafo_conocimiento.json
    if not GRAFO_JSON.exists():
        return []
    try:
        data = json.loads(GRAFO_JSON.read_text(encoding="utf-8"))
        nodos = data.get("nodos", {})
        resultados = []
        for n in nodos.values():
            if n.get("type") != "logro":
                continue
            skills = n.get("skills") or []
            hits = [
                s for s in skills
                if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", texto_low)
            ]
            if hits:
                resultados.append({
                    "id": n.get("id"),
                    "titulo": n.get("titulo"),
                    "ahorro": n.get("ahorro"),
                    "angulo": n.get("angulo"),
                    "skills_match": hits,
                })
        resultados.sort(key=lambda x: len(x["skills_match"]), reverse=True)
        return resultados[:5]
    except Exception:
        return []


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
1. Evalúa `elegible_chile_remoto` como TRUE solo si la oferta es compatible con la modalidad deseada ({modalidad_pref}) y el país/regiones del candidato ({pais} / {regiones}). Si exige residencia en otro país o viola la modalidad deseada -> elegible_chile_remoto=false, remoto_chile_20=0, prioridad="descartada", match_score <= 35.
2. Si `solo_ic` es true y el cargo es Manager, Director, VP, Head of, Scrum Master o Project Manager -> es_ic_tecnico=false, prioridad="descartada", match_score <= 25.
3. Si viola alguna exclusión dura del candidato ({exclusiones or 'ninguna'}) -> prioridad="descartada", match_score <= 30, motivo_descarte="Coincide con exclusión no negociable".
4. MEMORIA DE RECHAZO (AI EVALUATION): Si el rol es de AI Evaluator / AI Trainer pero pertenece a disciplinas científicas/académicas no informáticas (ej: Life Sciences, Physical Sciences, Mathematics, Biology, Chemistry, Physics, Medicine, Kaggle/Pure ML Research), márcala OBLIGATORIAMENTE como prioridad="descartada", match_score <= 30 y motivo_descarte="Disciplina ajena a perfil técnico (no afín a Cloud/DevOps/Software/Traducción)".
5. match_score debe ser coherente con la suma del desglose (clamp 0..100).
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
    es_exceso = sen_obj in ("junior", "mid") and (
        any(k in titulo for k in ["staff", "principal", "architect", "lead"])
        or bool(re.search(r"\b([5-9]|1[0-9])\+?\s*years?\b", desc))
    )
    es_senior = "senior" in titulo or "sr." in titulo or bool(re.search(r"\b4\+?\s*years?\b", desc))
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

    # 8. Exclusiones duras explícitas
    excl_hit = None
    for ex in pref.get("exclusiones") or []:
        ex_s = str(ex).strip().lower()
        if len(ex_s) >= 3 and ex_s not in ("presencial", "híbrido", "hibrido", "us only", "manager", "director"):
            if re.search(r"\b" + re.escape(ex_s) + r"\b", f"{titulo} {desc[:1500]}"):
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
    elif es_senior and sen_obj in ("junior", "mid") and raw_score >= 65:
        raw_score = min(raw_score, 72)
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
        # Asegurar coherencia de tipos y límites
        res["match_score"] = max(0, min(100, int(res.get("match_score", 0))))
        if not res.get("elegible_chile_remoto", False) or not res.get("es_ic_tecnico", True):
            res["prioridad"] = "descartada"
            res["match_score"] = min(res["match_score"], 35)
            if not res.get("motivo_descarte"):
                res["motivo_descarte"] = "No cumple filtros duros (remoto Chile/LatAm o rol IC)"
        elif res["match_score"] < 65:
            res["prioridad"] = "descartada"
            if not res.get("motivo_descarte"):
                res["motivo_descarte"] = f"Puntaje inferior al mínimo ({res['match_score']}/100)"
        return res
    except Exception as exc:
        fb = evaluar_heuristica(oferta, logros_grafo)
        fb["resumen_ia"] = f"Fallback heurístico (error en {prov['provider']}: {exc}). " + fb["resumen_ia"]
        return fb


def descargar_oferta_url(url: str) -> dict:
    """Descarga una página de oferta laboral y extrae título, empresa y texto limpio."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    m_title = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
    title_raw = strip_html(m_title.group(1)) if m_title else "Oferta Web"
    parts = re.split(r"\s+[-|–—]\s+", title_raw)
    titulo = parts[0].strip() if parts else title_raw
    empresa = parts[1].strip() if len(parts) > 1 else urllib.request.urlparse(url).netloc.replace("www.", "").split(".")[0].capitalize()

    return {
        "empresa": empresa,
        "titulo": titulo,
        "url": url,
        "portal": "URL Analizada",
        "ubicacion": "Ver descripción",
        "workplace_type": "",
        "descripcion": strip_html(raw)[:10000],
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
