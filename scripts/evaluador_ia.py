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
GRAFO_JSON = ROOT / "postulaciones" / "grafo_conocimiento.json"
AI_CONFIG_PATH = ROOT / "postulaciones" / ".ai-config.json"

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

    # 1. Si se pide un rol específico y tiene configuración propia en la matriz multi-agente
    role_cfg = (cfg.get("roles") or {}).get(rol, {}) if (rol and isinstance(cfg.get("roles"), dict)) else {}
    r_prov = (role_cfg.get("provider") or "").strip().lower()
    r_model = (role_cfg.get("model") or "").strip()

    if r_prov in candidatos and candidatos[r_prov]():
        return {"provider": r_prov, "model": r_model or modelos[r_prov](), "rol": rol}

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
    perfil = PERFIL_PATH.read_text(encoding="utf-8")[:4500] if PERFIL_PATH.exists() else ""

    grafo_txt = json.dumps(logros_grafo, ensure_ascii=False, indent=2) if logros_grafo else "[]"
    desc_limpia = (oferta.get("descripcion") or "")[:6500]

    return f"""Eres un evaluador estricto de ofertas laborales para el candidato descrito en el Perfil Maestro.
NO infles puntajes ni supongas que una oferta es remota global si no lo indica o si restringe residencia.

=== PREFERENCIAS Y RÚBRICA OBLIGATORIA ===
{preferencias}

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
  "modalidad_real": "string corta, ej: 100% Remoto (LatAm / Global) o Remoto solo EE.UU. o Híbrido (Austin, TX)",
  "es_ic_tecnico": true/false,
  "seniority_detectado": "Junior | Mid-Level | Semi-Senior | Senior | Staff/Lead/Principal | Manager",
  "idioma": "Inglés (C1) | Español | Bilingüe",
  "salario_detectado": "banda salarial si aparece en texto o 'Sueldo por confirmar'",
  "angulo": "finops | devsecops | secops-siem | ai-automation | base",
  "desglose": {{
    "tecnico_35": 0-35,
    "remoto_chile_20": 0-20,
    "ingles_15": 0-15,
    "salario_empresa_15": 0-15,
    "seniority_15": 0-15,
    "penalizacion_seniority": 0 a -25
  }},
  "match_score": 0-100,
  "prioridad": "alta | media | stretch | descartada",
  "motivo_descarte": null o "motivo claro si descartada (ej: Remote restringido a EE.UU., No es 100% remota, Exceso de Seniority 5+ años, Rol de gestión/Manager, Score <65)",
  "keywords": ["lista de 4-10 skills/keywords técnicos reales de la oferta"],
  "brechas": ["lista de brechas reales frente al perfil del candidato"],
  "logros_grafo_match": ["títulos exactos de los logros del grafo que calzan con esta oferta"],
  "resumen_ia": "2 frases concisas en español explicando exactamente por qué obtuvo este puntaje y si conviene postular."
}}

REGLAS CRÍTICAS:
1. Si la oferta exige residencia/permiso en US Only, EU Only, UK Only, Canada Only, o es Híbrida/Presencial -> elegible_chile_remoto=false, remoto_chile_20=0, prioridad="descartada", match_score <= 35.
2. Si es Engineering Manager, Director, VP, Head of, Scrum Master, Project Manager -> es_ic_tecnico=false, prioridad="descartada", match_score <= 25.
3. Si pide 5+ años excluyentes, Staff, Principal o Lead -> penalizacion_seniority=-25, prioridad="descartada" (o "stretch" máx 70 pts solo si >80% del stack coincide).
4. match_score debe ser coherente con la suma del desglose (clamp 0..100).
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
        "max_tokens": 1200,
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
    with urllib.request.urlopen(req, timeout=45) as resp:
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


def evaluar_heuristica(oferta: dict, logros_grafo: list[dict]) -> dict:
    """Evaluador determinista honesto (cuando no hay API key de IA configurada).
    Aplica la rúbrica de 5 criterios y filtros geográficos/seniority reales."""
    titulo = (oferta.get("titulo") or "").lower()
    ubic = (oferta.get("ubicacion") or "").lower()
    wp = (oferta.get("workplace_type") or "").lower()
    desc = (oferta.get("descripcion") or "").lower()
    full = f"{titulo} {ubic} {desc}"

    # 1. Filtro IC técnico
    es_manager = any(k in titulo for k in ["manager", "director", "head of", "vp ", "scrum master", "project manager"])

    # 2. Filtro Remoto + Chile/LatAm
    es_hibrido_presencial = (
        wp in ("hybrid", "onsite", "on-site")
        or bool(re.search(r"\b(hybrid|on-site|onsite|in-office|presencial|híbrido)\b", f"{ubic} {wp}"))
    )
    solo_otro_pais = bool(
        re.search(
            r"\b(us only|usa only|u\.s\. only|united states only|must reside in the u\.?s|authorized to work in the u\.?s|"
            r"eu only|europe only|uk only|united kingdom only|canada only|emea only|apac only|india only|"
            r"germany only|spain only|france only|australia only|brazil only|mexico only|colombia only|"
            r"us-based|based in the united states|residents of the us)\b",
            f"{ubic} {desc[:2500]}",
        )
    )
    permite_chile_latam = bool(
        re.search(
            r"\b(chile|latam|latin america|south america|worldwide|anywhere|global|americas|remote|remoto|international|b2b|deel)\b",
            f"{ubic} {desc[:2000]}",
        )
    )
    elegible = (not es_hibrido_presencial) and (not solo_otro_pais) and permite_chile_latam

    if es_hibrido_presencial:
        modalidad_real = f"Híbrido/Presencial ({oferta.get('ubicacion') or wp})"
    elif solo_otro_pais:
        modalidad_real = f"Remoto restringido ({oferta.get('ubicacion') or 'Otro país'})"
    elif elegible:
        modalidad_real = f"100% Remoto ({oferta.get('ubicacion') or 'LatAm / Global'})"
    else:
        modalidad_real = f"Ubicación no verificada ({oferta.get('ubicacion') or 'Sin especificar'})"

    # 3. Puntaje Técnico Core (0-35)
    tiene_finops = bool(re.search(r"\b(finops|cloud cost|cost optimization|savings plans|cur|cost explorer|cloud financial)\b", full))
    tiene_aws = bool(re.search(r"\b(aws|amazon web services|iam|guardduty|cloudwatch|ec2|s3|lambda|boto3)\b", full))
    tiene_secops = bool(re.search(r"\b(devsecops|secops|siem|wazuh|cloud security|incident response|guardduty)\b", full))
    tiene_ai = bool(re.search(r"\b(ai automation|llm|openai|claude|gemini|python|automation|n8n|workflows?)\b", full))

    if tiene_finops and tiene_aws:
        tecnico = 35
        angulo = "finops"
    elif tiene_finops:
        tecnico = 30
        angulo = "finops"
    elif tiene_secops and tiene_aws:
        tecnico = 28
        angulo = "secops-siem"
    elif tiene_aws and tiene_ai:
        tecnico = 26
        angulo = "ai-automation"
    elif tiene_aws:
        tecnico = 22
        angulo = "devsecops"
    elif tiene_ai:
        tecnico = 16
        angulo = "ai-automation"
    else:
        tecnico = 10
        angulo = "base"

    # 4. Remoto Chile (0-20)
    remoto_pts = 20 if elegible else 0

    # 5. Inglés (0-15)
    es_ingles = bool(re.search(r"\b(english|responsibilities|requirements|qualifications|benefits|apply)\b", full))
    idioma = "Inglés (C1)" if es_ingles else "Español"
    ingles_pts = 15 if es_ingles else 10

    # 6. Salario / Empresa (0-15)
    m_sal = re.search(
        r"(\$\s?\d{1,3}(?:,\d{3})+(?:\s*[-–to]+\s*\$?\s?\d{1,3}(?:,\d{3})+)?(?:\s*(?:USD|CLP|/yr|/year|/mo|/month))?)",
        oferta.get("descripcion") or "",
        re.I,
    )
    salario_txt = m_sal.group(1) if m_sal else (oferta.get("salario") or "Sueldo por confirmar")
    salario_pts = 12 if (es_ingles or m_sal) else 9

    # 7. Seniority (0-15 + penalización)
    es_exceso = any(k in titulo for k in ["staff", "principal", "architect", "lead"]) or bool(
        re.search(r"\b([5-9]|1[0-9])\+?\s*years?\b", desc)
    )
    es_senior = "senior" in titulo or "sr." in titulo or bool(re.search(r"\b4\+?\s*years?\b", desc))
    if es_manager:
        seniority_det = "Manager"
        seniority_pts, pen = 0, -25
    elif es_exceso:
        seniority_det = "Staff/Lead/Principal"
        seniority_pts, pen = 0, -25
    elif es_senior:
        seniority_det = "Senior"
        seniority_pts, pen = 5, -10
    else:
        seniority_det = "Mid-Level"
        seniority_pts, pen = 15, 0

    raw_score = max(0, min(100, tecnico + remoto_pts + ingles_pts + salario_pts + seniority_pts + pen))
    motivo = None
    if es_manager:
        raw_score = min(raw_score, 25)
        prioridad = "descartada"
        motivo = "Rol de gestión/Manager (buscamos IC técnico)"
    elif not elegible:
        raw_score = min(raw_score, 35)
        prioridad = "descartada"
        motivo = f"No cumple 100% remoto elegible desde Chile ({modalidad_real})"
    elif es_exceso:
        prioridad = "descartada"
        motivo = "Exceso de seniority (Staff/Lead/Principal o 5+ años excluyentes)"
    elif raw_score >= 80 and not es_senior:
        prioridad = "alta"
    elif es_senior and raw_score >= 65:
        raw_score = min(raw_score, 72)
        prioridad = "stretch"
    elif raw_score >= 65:
        prioridad = "media"
    else:
        prioridad = "descartada"
        motivo = f"Score insuficiente ({raw_score}/100) según rúbrica"

    brechas = []
    if es_senior or es_exceso:
        brechas.append("Pide perfil Senior / más años de experiencia")
    if not tiene_finops:
        brechas.append("Sin mención explícita a FinOps / Cloud Cost Optimization")
    for tool in ["terraform", "kubernetes", "golang", "spark", "azure", "gcp"]:
        if re.search(rf"\b{tool}\b", full):
            brechas.append(f"Menciona {tool.capitalize()} (verificar nivel exigido)")

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
        "keywords": oferta.get("keywords_matched") or [],
        "brechas": brechas[:5] or ["Verificar requisitos en detalle"],
        "logros_grafo_match": [l["titulo"] for l in logros_grafo[:4]],
        "resumen_ia": (
            f"Evaluación heurística (sin API key configurada): {raw_score}/100. "
            f"{'Cumple remoto elegible.' if elegible else 'Descartada por ubicación/modalidad.'} "
            f"Configura GEMINI_API_KEY en .env para evaluación semántica completa."
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
