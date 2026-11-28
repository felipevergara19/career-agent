#!/usr/bin/env python3
"""Recolector multi-fuente de vacantes 100% remotas elegibles desde Chile/LatAm.

Fuentes (APIs públicas, 0 tokens de captura):
  - ATS oficiales: Greenhouse (?content=true), Ashby (?includeCompensation=true), Lever
  - Portales remotos: Get on Board (LatAm/Chile), RemoteOK, Remotive, Himalayas

Flujo:
  1. Recolecta vacantes con descripción completa y ubicación/modalidad real.
  2. Aplica filtros duros de `perfil/preferencias.md` (descarta híbrido/presencial,
     US/EU/UK-only y roles Manager/Director/Staff/Principal).
  3. Deduplica con `scripts/estado.py existe` ANTES de invocar IA.
  4. Evalúa las candidatas reales con `scripts/evaluador_ia.py` (Neo4j GraphRAG + LLM
     configurado en .env, o heurística honesta sin hardcodear 85).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import evaluador_ia  # noqa: E402

CATALOGO_PATH = ROOT / "perfil" / "empresas-target.yaml"
STRUCTURED_PERFIL_PATH = ROOT / "perfil" / "perfil-estructurado.json"
SEEN_CACHE_PATH = ROOT / "postulaciones" / ".seen-urls.json"
SEEN_TTL_DIAS = 21
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
_ATS_ERRORS: list[str] = []

# --------------------------------------------------------------- Keywords
# Términos de ROL: cuentan solo si aparecen en el TÍTULO (señal fuerte de que el puesto es para Felipe).
TITULO_FINOPS = ["finops", "cloud cost", "cost optimization", "cloud financial", "cloud economics",
                 "cost engineer", "cost analyst", "costos cloud", "costos en la nube"]
TITULO_DEVOPS_AWS = ["aws", "cloud engineer", "cloud operations", "cloudops", "cloud analyst", "cloud specialist",
                     "cloud consultant", "cloud support", "infrastructure engineer", "infra engineer", "devops",
                     "platform engineer", "sre", "site reliability", "observability", "ingeniero cloud",
                     "ingeniero devops", "nube", "infraestructura", "cloud"]
TITULO_SECOPS = ["devsecops", "secops", "cloud security", "security engineer", "security analyst", "soc analyst",
                 r"\bsoc\b", "siem", "wazuh", "detection", "incident response", "cybersecurity", "cyber security",
                 "ciberseguridad", "seguridad", "security operations", "iam engineer", "identity"]
TITULO_AI_AUTO = ["ai automation", "automation engineer", "workflow automation", "n8n", "ai engineer",
                  "llm engineer", "agentic", "automatizacion", "automatización"]
TITULO_AI_EVAL = ["ai trainer", "ai tutor", "ai evaluator", "ai rater", "rlhf", "prompt engineer",
                  "prompt evaluator", "code evaluator", "coding expert", "language expert",
                  "data annotator", "ai quality", "model evaluator", "llm evaluator", "bilingual spanish"]

# Términos de STACK: suman si aparecen en la descripción (nunca bastan solos para un título no técnico).
STACK_KEYWORDS = ["aws", "finops", "cost explorer", r"\bcur\b", "savings plans", "reserved instances", "kubecost",
                  "terraform", "kubernetes", r"\beks\b", "cloudwatch", "guardduty", "security hub", r"\biam\b",
                  "wazuh", "siem", "opensearch", "python", "boto3", "lambda", "eventbridge", "n8n", "llm",
                  "github actions", "devsecops", "incident response", "cloud cost", "cost optimization",
                  "rlhf", "prompt engineering", "model evaluation", "rubric", "data annotation"]

KEYWORDS_FINOPS = TITULO_FINOPS
KEYWORDS_DEVOPS_AWS = TITULO_DEVOPS_AWS
KEYWORDS_SECOPS = TITULO_SECOPS
KEYWORDS_AI_AUTO = TITULO_AI_AUTO
KEYWORDS_AI_EVAL = TITULO_AI_EVAL
ALL_KEYWORDS = TITULO_FINOPS + TITULO_DEVOPS_AWS + TITULO_SECOPS + TITULO_AI_AUTO + TITULO_AI_EVAL

RE_TITULO_TECNICO = re.compile(
    r"\b(engineer|engineering|analyst|developer|specialist|architect|consultant|administrator|admin|sre|"
    r"devops|ingenier[oa]|analista|desarrollador[a]?|especialista|arquitect[oa]|consultor[a]?|"
    r"ai trainer|ai tutor|ai evaluator|ai rater|rlhf|prompt|code evaluator)\b",
    re.I,
)
RE_TITULO_NO_TECNICO = re.compile(
    r"\b(accountant|accounting|marketing|designer|content|copywriter|writer|talent|recruit\w*|legal|counsel|"
    r"customer success|account manager|account executive|sales|business development|payroll|people partner|"
    r"hr\b|human resources|office|assistant|bookkeeper|controller|tax|treasury|translator|teacher|tutor|nurse)\b",
    re.I,
)
RE_SENIOR_ALTO = re.compile(r"\b(senior|sr\.?)\b", re.I)
RE_JUNIOR_MID = re.compile(r"\b(junior|jr\.?|mid|semi[- ]?senior|ssr|associate|analyst|analista|ii\b|i\b)", re.I)
RE_ANOS_ALTOS = re.compile(r"\b([5-9]|1[0-9])\s*\+?\s*(years|años|yrs)", re.I)
RE_REGION_OK = re.compile(r"\b(chile|santiago|latam|latin america|south america|americas|worldwide|anywhere|global|remoto)\b", re.I)

_PREF_CACHE: dict | None = None


def cargar_preferencias_usuario() -> dict:
    """Preferencias del usuario: perfil/perfil-estructurado.json si existe; si no, se derivan
    de perfil/preferencias.md vía perfil_builder (antes este fallback no existía y se ignoraban)."""
    global _PREF_CACHE
    if _PREF_CACHE is not None:
        return _PREF_CACHE
    pref: dict = {}
    if STRUCTURED_PERFIL_PATH.is_file():
        try:
            data = json.loads(STRUCTURED_PERFIL_PATH.read_text(encoding="utf-8"))
            pref = (data.get("preferencias") or {}) if isinstance(data, dict) else {}
        except Exception:
            pref = {}
    if not pref:
        try:
            import perfil_builder

            pref = (perfil_builder.cargar_perfil_estructurado() or {}).get("preferencias") or {}
        except Exception:
            pref = {}
    _PREF_CACHE = pref
    return pref


def obtener_keywords_dinamicas(foco: str = "todos") -> list[str]:
    """Keywords de TÍTULO por foco. Se combinan las del código con las áreas/puestos del perfil."""
    base = {
        "finops": TITULO_FINOPS,
        "secops": TITULO_SECOPS,
        "secops-siem": TITULO_SECOPS,
        "ai": TITULO_AI_AUTO,
        "ai-automation": TITULO_AI_AUTO,
        "ai-evaluator": TITULO_AI_EVAL,
        "rlhf": TITULO_AI_EVAL,
        "devsecops": TITULO_DEVOPS_AWS + ["devsecops", "cloud security"],
    }.get(foco, ALL_KEYWORDS)
    pref = cargar_preferencias_usuario()
    extra: list[str] = []
    for a in pref.get("areas") or []:
        if foco in ("", "todos") or (a.get("slug") or "").lower() == foco.lower():
            extra.extend(str(k).strip().lower() for k in (a.get("keywords") or []) if str(k).strip())
    # Términos genéricos del perfil (python, llm, iam...) se usan como STACK, no como rol de título.
    extra = [k for k in extra if k not in ("python", "llm", "rag", "boto3", "iam", "cur", "terraform")]
    return list(dict.fromkeys([*base, *extra]))


def _buscar_terminos(terminos: list[str], texto: str) -> list[str]:
    hits = []
    for kw in terminos:
        pattern = kw if kw.startswith(r"\b") else r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, texto, re.IGNORECASE):
            hits.append(kw.replace(r"\b", ""))
    return hits


def pre_puntaje(job: dict, kws_titulo: list[str]) -> tuple[int, list[str], list[str]]:
    """Puntaje determinista (0 tokens) para ordenar candidatas antes de gastar IA.
    Devuelve (puntaje, keywords_en_titulo, stack_en_descripcion)."""
    titulo = job.get("titulo") or ""
    empresa = (job.get("empresa") or "").lower()
    desc = (job.get("descripcion") or "")[:4000]
    if empresa and len(empresa) >= 3:  # que "n8n" no matchee solo por ser el nombre de la empresa
        desc = re.sub(re.escape(empresa), " ", desc, flags=re.I)
    t_hits = _buscar_terminos(kws_titulo, titulo)
    s_hits = _buscar_terminos(STACK_KEYWORDS, desc)

    score = 0
    score += min(45, 25 * len(t_hits))
    score += min(30, 4 * len(s_hits))
    if any(k in titulo.lower() for k in ("finops", "cloud cost", "cost optim")):
        score += 10  # nicho #1 de Felipe
    if RE_SENIOR_ALTO.search(titulo):
        score -= 8
    elif RE_JUNIOR_MID.search(titulo):
        score += 6
    if RE_ANOS_ALTOS.search(desc[:3000]):
        score -= 8
    if RE_REGION_OK.search(job.get("ubicacion") or ""):
        score += 12
    if job.get("ats") == "getonboard":
        score += 5
    return max(0, min(100, score)), t_hits, s_hits


RE_EXCLUIR_TITULO = re.compile(
    r"\b(manager|director|vp\b|vice president|head of|chief|principal|staff\b|lead\b|distinguished|"
    r"scrum master|project manager|product manager|account executive|sales|recruiter|counsel|intern)\b",
    re.I,
)
RE_HIBRIDO_PRESENCIAL = re.compile(
    r"\b(hybrid|on-site|onsite|in-office|in person|presencial|híbrido|hibrido)\b",
    re.I,
)
RE_SOLO_OTRO_PAIS = re.compile(
    r"\b(us only|usa only|u\.s\. only|united states only|u\.s\.\s*remote|remote\s*[-–—(]?\s*(us|usa|u\.s\.|united states|uk|united kingdom|eu|europe|canada|emea|apac|india|germany|france|spain|australia|brazil|mexico|colombia|argentina|poland|ireland)\)?$|"
    r"must reside in the u\.?s|must be located in the u\.?s|must be based in the u\.?s|"
    r"authorized to work in the u\.?s|us citizens? only|usc\s+(and|or|&|/)\s*gc|green card|security clearance|"
    r"eu only|europe only|uk only|united kingdom only|canada only|emea only|apac only|india only|"
    r"germany only|spain only|france only|australia only|brazil only|mexico only|colombia only|"
    r"united states \(remote\)|remote - us\b|remote, us\b|remote \(us\)|remote \(usa\)|remote - uk\b|remote - eu\b|remote - canada\b|remote - emea\b)\b",
    re.I,
)
RE_PAIS_UNICO_EXCLUIDO = re.compile(
    r"^\s*(united states|usa|u\.s\.a?\.|canada|united kingdom|uk|germany|france|italy|netherlands|poland|"
    r"ireland|portugal|australia|india|singapore|japan|brazil|brasil|mexico|méxico|colombia|argentina|peru|perú|"
    r"china|bulgaria|ukraine|latvia|lithuania|estonia|romania|serbia|croatia|hungary|czechia|czech republic|"
    r"austria|switzerland|belgium|sweden|norway|denmark|finland|greece|turkey|israel|united arab emirates|uae|dubai|"
    r"south africa|nigeria|kenya|egypt|morocco|philippines|vietnam|indonesia|malaysia|thailand|taiwan|hong kong|"
    r"south korea|korea|pakistan|bangladesh|"
    r"united states,\s*canada|canada,\s*united states|europe|emea|apac)\s*$",
    re.I,
)
RE_GLOBAL_EXPLICITO = re.compile(
    r"\b(anywhere in the world|home based\s*[-–]?\s*worldwide|work from anywhere|remote\s*[-–,]?\s*(worldwide|global|anywhere|latam|latin america|americas|amer\b|chile))\b",
    re.I,
)
RE_CIUDAD_SIN_REMOTO = re.compile(
    r"\b(san francisco|new york|austin|seattle|boston|chicago|los angeles|london|berlin|paris|amsterdam|dublin|toronto|vancouver|sydney|singapore|tokyo|bangalore|bengaluru|tel aviv|madrid|barcelona|lisbon|munich|zurich|denver|atlanta|miami|dallas|washington,?\s*dc|raleigh|salt lake|menlo park|mountain view|palo alto)\b",
    re.I,
)
RE_CHILE_LATAM_GLOBAL = re.compile(
    r"\b(chile|santiago|latam|latin america|south america|worldwide|anywhere|global|americas|amer\b|international|remote worldwide|work from anywhere|remoto)\b",
    re.I,
)


def cargar_catalogo() -> dict:
    if not CATALOGO_PATH.exists():
        return {"greenhouse": [], "ashby": [], "lever": []}
    return yaml.safe_load(CATALOGO_PATH.read_text(encoding="utf-8")) or {}


def guardar_catalogo(data: dict) -> None:
    CATALOGO_PATH.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def agregar_empresa(ats: str, slug: str) -> None:
    cat = cargar_catalogo()
    ats = ats.lower()
    cat.setdefault(ats, [])
    slug = slug.strip().lower()
    if slug not in cat[ats]:
        cat[ats].append(slug)
        guardar_catalogo(cat)
        print(f"✓ Agregada empresa '{slug}' a {ats} en {CATALOGO_PATH}")
    else:
        print(f"ℹ '{slug}' ya estaba registrada en {ats}")


def descubrir_desde_url(url: str) -> tuple[str | None, str | None]:
    url = url.strip()
    for ats, pattern in [
        ("greenhouse", r"(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io)/([^/]+)"),
        ("ashby", r"(?:jobs\.ashbyhq\.com|app\.ashbyhq\.com)/([^/]+)"),
        ("lever", r"jobs\.lever\.co/([^/]+)"),
        ("workable", r"apply\.workable\.com/([^/]+)"),
        ("recruitee", r"https?://([a-z0-9-]+)\.recruitee\.com"),
        ("rippling", r"ats\.rippling\.com/(?:[a-z]{2}/)?([^/]+)/jobs"),
        ("pinpoint", r"https?://([a-z0-9-]+)\.pinpointhq\.com"),
    ]:
        m = re.search(pattern, url, re.I)
        if m:
            slug = m.group(1)
            agregar_empresa(ats, slug)
            return ats, slug
    print(f"⚠ No se reconoció formato ATS en URL: {url}")
    return None, None


def _get_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _parse_fecha_iso(val) -> str | None:
    """Normaliza timestamps ISO, epoch ms (Lever) o RFC-2822 (RSS) a 'YYYY-MM-DD'."""
    if not val:
        return None
    try:
        if isinstance(val, (int, float)):
            ts = val / 1000.0 if val > 1e11 else float(val)
            return datetime.date.fromtimestamp(ts).strftime("%Y-%m-%d")
        s = str(val).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).date().strftime("%Y-%m-%d")
    except Exception:
        return None


# ------------------------------------------------------------------ ATS APIs
def consultar_greenhouse(slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        data = _get_json(url, timeout=12)
        jobs = []
        for j in data.get("jobs", []):
            ubic = (j.get("location") or {}).get("name") or ""
            desc = evaluador_ia.strip_html(j.get("content") or "")
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Oficial (Greenhouse)",
                "ats": "greenhouse",
                "id": str(j.get("id")),
                "titulo": j.get("title") or "",
                "ubicacion": ubic,
                "workplace_type": "remote" if "remote" in ubic.lower() else "",
                "fecha_publicacion": _parse_fecha_iso(j.get("updated_at")),
                "url": j.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{j.get('id')}",
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"greenhouse:{slug} ({str(exc)[:45]})")
        return []


def consultar_ashby(slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    try:
        data = _get_json(url, timeout=12)
        jobs = []
        for j in data.get("jobs", []):
            ubic = j.get("location") or j.get("locationName") or ""
            sec = [s.get("location") for s in (j.get("secondaryLocations") or []) if isinstance(s, dict) and s.get("location")]
            if sec:
                ubic = f"{ubic} | {', '.join(sec[:3])}".strip(" |")
            wp = str(j.get("workplaceType") or "")
            if j.get("isRemote") and not wp:
                wp = "Remote"
            desc = j.get("descriptionPlain") or evaluador_ia.strip_html(j.get("descriptionHtml") or "")
            comp = j.get("compensationTierSummary") or ""
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Oficial (Ashby)",
                "ats": "ashby",
                "id": str(j.get("id")),
                "titulo": j.get("title") or "",
                "ubicacion": ubic,
                "workplace_type": wp,
                "salario": comp or "Sueldo por confirmar",
                "fecha_publicacion": _parse_fecha_iso(j.get("publishedAt")),
                "url": j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}",
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"ashby:{slug} ({str(exc)[:45]})")
        return []


def consultar_lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        data = _get_json(url, timeout=12)
        jobs = []
        for j in data:
            cats = j.get("categories") or {}
            ubic = cats.get("location") or ""
            wp = j.get("workplaceType") or ""
            desc = j.get("descriptionPlain") or evaluador_ia.strip_html(j.get("description") or "")
            extra_lists = " ".join(
                evaluador_ia.strip_html(l.get("content") or "") for l in (j.get("lists") or [])
            )
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Oficial (Lever)",
                "ats": "lever",
                "id": str(j.get("id")),
                "titulo": j.get("text") or "",
                "ubicacion": ubic,
                "workplace_type": wp,
                "fecha_publicacion": _parse_fecha_iso(j.get("createdAt")),
                "url": j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j.get('id')}",
                "descripcion": f"{desc} {extra_lists}"[:9000],
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"lever:{slug} ({str(exc)[:45]})")
        return []


def consultar_teamtailor(slug: str) -> list[dict]:
    """RSS público de Teamtailor (muy usado por fintechs top de Chile: Global66, Tenpo, Betterfly, Neat)."""
    url = f"https://{slug}.teamtailor.com/jobs.rss"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            root = ET.fromstring(resp.read().decode("utf-8", errors="ignore"))
        jobs = []
        for item in root.findall(".//item"):
            titulo = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = evaluador_ia.strip_html(item.findtext("description") or "")
            # Teamtailor suele incluir ubicación o remoteStatus en namespaces tt:
            ubic = "Chile / LatAm"
            wp = ""
            for child in item:
                tag = child.tag.lower()
                txt = (child.text or "").strip()
                if tag.endswith("remotestatus") and txt:
                    wp = "remote" if "remote" in txt.lower() or "fully" in txt.lower() else txt.lower()
                elif tag.endswith("location") and txt:
                    ubic = txt
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Chile (Teamtailor)",
                "ats": "teamtailor",
                "id": link,
                "titulo": titulo,
                "ubicacion": f"Remoto ({ubic})" if wp == "remote" else ubic,
                "workplace_type": wp,
                "url": link,
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception:
        return []


def consultar_breezy(slug: str) -> list[dict]:
    url = f"https://{slug}.breezy.hr/json"
    try:
        data = _get_json(url, timeout=10)
        jobs = []
        for j in data if isinstance(data, list) else []:
            loc = j.get("location") or {}
            is_rem = bool(loc.get("is_remote"))
            ubic = loc.get("name") or "Chile"
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Chile (Breezy)",
                "ats": "breezy",
                "id": str(j.get("id")),
                "titulo": j.get("name") or "",
                "ubicacion": f"Remoto ({ubic})" if is_rem else ubic,
                "workplace_type": "remote" if is_rem else "",
                "url": j.get("url") or "",
                "descripcion": f"{j.get('name', '')} {ubic} {(j.get('department') or '')}",
            })
        return jobs
    except Exception:
        return []


def consultar_smartrecruiters(slug: str) -> list[dict]:
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=50"
    try:
        data = _get_json(url, timeout=10)
        jobs = []
        for j in data.get("content", []):
            loc = j.get("location") or {}
            city = loc.get("city") or ""
            country = (loc.get("country") or "").upper()
            is_rem = bool(loc.get("remote"))
            ubic = ", ".join(p for p in [city, country] if p) or "Global"
            if country == "CL":
                ubic = f"Chile ({ubic})"
            pid = j.get("id")
            titulo = j.get("name") or ""
            dept = (j.get("department") or {}).get("label", "")
            desc = f"{titulo} {ubic} {dept}"
            # Enriquecer descripción completa desde el endpoint de detalle solo para roles técnicos candidatos
            if pid and (is_rem or country in ("CL", "")) and RE_TITULO_TECNICO.search(titulo):
                try:
                    det = _get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}", timeout=8)
                    sec = ((det.get("jobAd") or {}).get("sections") or {})
                    partes = [
                        (sec.get(k) or {}).get("text") or ""
                        for k in ("jobDescription", "qualifications", "additionalInformation")
                    ]
                    full_desc = evaluador_ia.strip_html(" ".join(partes))
                    if full_desc:
                        desc = full_desc[:9000]
                except Exception:
                    pass
            jobs.append({
                "empresa": (j.get("company") or {}).get("name") or slug.capitalize(),
                "portal": "ATS Oficial (SmartRecruiters)",
                "ats": "smartrecruiters",
                "id": str(pid),
                "titulo": titulo,
                "ubicacion": f"Remoto ({ubic})" if is_rem else ubic,
                "workplace_type": "remote" if is_rem else "",
                "url": f"https://jobs.smartrecruiters.com/{slug}/{pid}",
                "descripcion": desc,
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"smartrecruiters:{slug} ({str(exc)[:45]})")
        return []


def consultar_workable(slug: str) -> list[dict]:
    """Widget JSON público de Workable + detalle JSON v2 para obtener descripción completa."""
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    try:
        data = _get_json(url, timeout=10)
        emp = data.get("name") or slug.capitalize()
        jobs = []
        for j in data.get("jobs", []):
            is_rem = bool(j.get("telecommuting"))
            country = j.get("country") or ""
            city = j.get("city") or ""
            loc_parts = [p for p in (city, country) if p]
            loc_txt = ", ".join(loc_parts) or "Remote"
            ubic = f"Remote ({loc_txt})" if is_rem and loc_parts else ("Remote" if is_rem else loc_txt)
            titulo = j.get("title") or ""
            sc = str(j.get("shortcode") or "").strip()
            desc = f"{titulo} {j.get('department', '')} {j.get('function', '')} {j.get('experience', '')} {loc_txt}"
            if sc and is_rem and RE_TITULO_TECNICO.search(titulo):
                try:
                    det = _get_json(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{sc}", timeout=8)
                    full_desc = evaluador_ia.strip_html(
                        f"{det.get('description') or ''} {det.get('requirements') or ''} {det.get('benefits') or ''}"
                    )
                    if full_desc:
                        desc = full_desc[:9000]
                except Exception:
                    pass
            jobs.append({
                "empresa": emp,
                "portal": "ATS Oficial (Workable)",
                "ats": "workable",
                "id": sc or str(j.get("url") or ""),
                "titulo": titulo,
                "ubicacion": ubic,
                "workplace_type": "remote" if is_rem else "",
                "url": j.get("url") or j.get("shortlink") or "",
                "descripcion": desc,
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"workable:{slug} ({str(exc)[:45]})")
        return []


def consultar_recruitee(slug: str) -> list[dict]:
    """API pública JSON de Recruitee (https://<slug>.recruitee.com/api/offers/)."""
    url = f"https://{slug}.recruitee.com/api/offers/"
    try:
        data = _get_json(url, timeout=12)
        jobs = []
        for j in data.get("offers", []):
            is_rem = bool(j.get("remote"))
            is_hyb = bool(j.get("hybrid"))
            ubic = j.get("location") or ", ".join(p for p in (j.get("city"), j.get("country")) if p) or ("Remote" if is_rem else "")
            wp = "hybrid" if is_hyb else ("remote" if is_rem else "")
            desc = evaluador_ia.strip_html(f"{j.get('description') or ''} {j.get('requirements') or ''}")
            jobs.append({
                "empresa": j.get("company_name") or slug.capitalize(),
                "portal": "ATS Oficial (Recruitee)",
                "ats": "recruitee",
                "id": str(j.get("id") or j.get("slug") or ""),
                "titulo": j.get("title") or "",
                "ubicacion": ubic,
                "workplace_type": wp,
                "fecha_publicacion": _parse_fecha_iso(j.get("published_at") or j.get("created_at")),
                "url": j.get("careers_url") or f"https://{slug}.recruitee.com/o/{j.get('slug', '')}",
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"recruitee:{slug} ({str(exc)[:45]})")
        return []


def consultar_rippling(slug: str) -> list[dict]:
    """API pública JSON de Rippling ATS (https://api.rippling.com/platform/api/ats/v1/board/<slug>/jobs)."""
    url = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    try:
        data = _get_json(url, timeout=12)
        items = data if isinstance(data, list) else (data.get("items") or data.get("jobs") or [])
        jobs = []
        for j in items:
            uid = str(j.get("uuid") or j.get("id") or "").strip()
            titulo = j.get("name") or j.get("title") or ""
            loc_obj = j.get("workLocation") or {}
            ubic = (loc_obj.get("label") if isinstance(loc_obj, dict) else str(loc_obj or "")) or ""
            dept_obj = j.get("department") or {}
            dept = dept_obj.get("label") if isinstance(dept_obj, dict) else str(dept_obj or "")
            is_rem = "remote" in ubic.lower()
            desc = f"{titulo} {dept} {ubic}".strip()
            if uid and is_rem and RE_TITULO_TECNICO.search(titulo):
                try:
                    det = _get_json(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uid}", timeout=8)
                    full_d = evaluador_ia.strip_html(
                        f"{det.get('description', {}).get('role', '') if isinstance(det.get('description'), dict) else det.get('description') or ''} "
                        f"{det.get('description', {}).get('company', '') if isinstance(det.get('description'), dict) else ''}"
                    )
                    if full_d:
                        desc = full_d[:9000]
                except Exception:
                    pass
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Oficial (Rippling)",
                "ats": "rippling",
                "id": uid,
                "titulo": titulo,
                "ubicacion": ubic,
                "workplace_type": "remote" if is_rem else "",
                "url": j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{uid}",
                "descripcion": desc,
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"rippling:{slug} ({str(exc)[:45]})")
        return []


def consultar_pinpoint(slug: str) -> list[dict]:
    """API pública JSON de Pinpoint (https://<slug>.pinpointhq.com/postings.json)."""
    url = f"https://{slug}.pinpointhq.com/postings.json"
    try:
        data = _get_json(url, timeout=12)
        jobs = []
        for j in data.get("data", []):
            loc = j.get("location") or {}
            ubic = loc.get("name") or ", ".join(p for p in (loc.get("city"), loc.get("country")) if p) or ""
            wp_raw = str(j.get("workplace_type") or "").lower()
            is_rem = wp_raw == "remote" or "remote" in ubic.lower()
            desc = evaluador_ia.strip_html(
                f"{j.get('description') or ''} {j.get('requirements') or ''} {j.get('responsibilities') or ''}"
            )
            jobs.append({
                "empresa": slug.capitalize(),
                "portal": "ATS Oficial (Pinpoint)",
                "ats": "pinpoint",
                "id": str(j.get("id") or ""),
                "titulo": j.get("title") or "",
                "ubicacion": ubic,
                "workplace_type": "remote" if is_rem else wp_raw,
                "url": j.get("url") or f"https://{slug}.pinpointhq.com/jobs/{j.get('id', '')}",
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception as exc:
        _ATS_ERRORS.append(f"pinpoint:{slug} ({str(exc)[:45]})")
        return []


def consultar_finops_foundation() -> list[dict]:
    """API REST pública de FinOps Foundation Job Board (jobs.finops.org/wp-json/wp/v2/vacancies).
    Extrae jobLocationType (TELECOMMUTE vs presencial) y jobLocation / applicantLocationRequirements reales."""
    jobs: list[dict] = []
    try:
        for page in (1, 2):
            data = _get_json(f"https://jobs.finops.org/wp-json/wp/v2/vacancies?per_page=50&page={page}", timeout=12)
            if not isinstance(data, list) or not data:
                break
            for item in data:
                link = str(item.get("link") or "").strip()
                titulo = evaluador_ia.strip_html((item.get("title") or {}).get("rendered") or "").strip()
                desc = evaluador_ia.strip_html((item.get("content") or {}).get("rendered") or "")
                empresa = "FinOps Foundation Board"
                ubic = ""
                wp = ""
                graph = (((item.get("yoast_head_json") or {}).get("schema") or {}).get("@graph")) or []
                for node in graph:
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        org = (node.get("hiringOrganization") or {}).get("name")
                        if org:
                            empresa = evaluador_ia.strip_html(str(org)).strip()
                        if node.get("description") and not desc:
                            desc = evaluador_ia.strip_html(str(node["description"]))
                        is_tele = str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE"
                        req_loc = node.get("applicantLocationRequirements")
                        reqs = []
                        if isinstance(req_loc, dict) and req_loc.get("name"):
                            reqs.append(str(req_loc["name"]))
                        elif isinstance(req_loc, list):
                            reqs.extend(str(x.get("name")) for x in req_loc if isinstance(x, dict) and x.get("name"))
                        jloc = node.get("jobLocation")
                        jloc_parts = []
                        if isinstance(jloc, dict):
                            addr = jloc.get("address") or {}
                            if isinstance(addr, dict):
                                jloc_parts = [str(addr.get(k) or "").strip() for k in ("addressLocality", "addressCountry") if addr.get(k)]
                        req_str = ", ".join(reqs) or ", ".join(jloc_parts)
                        if is_tele:
                            wp = "remote"
                            ubic = f"Remote ({req_str})" if req_str else "Remote Worldwide"
                        else:
                            wp = "onsite"
                            ubic = req_str or "Onsite"
                        break
                if link and titulo:
                    jobs.append({
                        "empresa": empresa,
                        "portal": "FinOps Foundation Jobs",
                        "ats": "finops_org",
                        "id": str(item.get("id") or link),
                        "titulo": titulo,
                        "ubicacion": ubic,
                        "workplace_type": wp,
                        "salario": "Sueldo por confirmar",
                        "url": link,
                        "descripcion": desc[:9000],
                    })
            if len(data) < 50:
                break
    except Exception as exc:
        _ATS_ERRORS.append(f"finops.org ({str(exc)[:45]})")
    return jobs


def consultar_wazuh() -> list[dict]:
    """Rastrea la sección 'Open roles' de Wazuh Official Careers (100% Remote Global)."""
    jobs: list[dict] = []
    try:
        req = urllib.request.Request("https://wazuh.com/careers/", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")
        # Tomar únicamente la sección 'Open roles' (antes de 'Upcoming positions')
        open_sec = html_raw
        if 'Open <span class="text-bold">roles</span>' in html_raw:
            open_sec = html_raw.split('Open <span class="text-bold">roles</span>', 1)[1]
        if 'Upcoming <span class="text-bold">positions</span>' in open_sec:
            open_sec = open_sec.split('Upcoming <span class="text-bold">positions</span>', 1)[0]
        links = list(dict.fromkeys(re.findall(r'href=["\'](https://wazuh\.com/job/[^"\']+)["\']', open_sec)))
        for link in links[:15]:
            try:
                r2 = urllib.request.Request(link, headers=HEADERS)
                with urllib.request.urlopen(r2, timeout=8) as resp2:
                    jhtml = resp2.read().decode("utf-8", errors="ignore")
                m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", jhtml, re.I | re.S)
                titulo = evaluador_ia.strip_html(m_h1.group(1)).strip() if m_h1 else link.rstrip("/").split("/")[-1].replace("-", " ").title()
                jobs.append({
                    "empresa": "Wazuh",
                    "portal": "Wazuh Official Careers",
                    "ats": "wazuh",
                    "id": link,
                    "titulo": titulo,
                    "ubicacion": "100% Remote Worldwide",
                    "workplace_type": "remote",
                    "salario": "Sueldo por confirmar",
                    "url": link,
                    "descripcion": evaluador_ia.strip_html(jhtml)[:9000],
                })
            except Exception:
                continue
    except Exception as exc:
        _ATS_ERRORS.append(f"wazuh ({str(exc)[:45]})")
    return jobs


def consultar_workingnomads() -> list[dict]:
    """API pública JSON de Working Nomads (remoto global)."""
    jobs: list[dict] = []
    try:
        data = _get_json("https://www.workingnomads.com/api/exposed_jobs/", timeout=12)
        for j in data if isinstance(data, list) else []:
            cat = (j.get("category_name") or "").lower()
            if cat and cat not in ("development", "system administration", "devops", "security", "management"):
                continue
            jobs.append({
                "empresa": j.get("company_name") or "WorkingNomads",
                "portal": "Working Nomads",
                "ats": "workingnomads",
                "id": str(j.get("url") or ""),
                "titulo": j.get("title") or "",
                "ubicacion": j.get("location") or "Worldwide",
                "workplace_type": "remote",
                "salario": "Sueldo por confirmar",
                "url": j.get("url") or "",
                "descripcion": evaluador_ia.strip_html(f"{j.get('tags') or ''} {j.get('description') or ''}")[:9000],
            })
    except Exception:
        pass
    return jobs


def consultar_wwr() -> list[dict]:
    """We Work Remotely RSS feeds (DevOps/SysAdmin y Backend)."""
    feeds = [
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    ]
    jobs: list[dict] = []
    for feed in feeds:
        try:
            req = urllib.request.Request(feed, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                root = ET.fromstring(resp.read().decode("utf-8", errors="ignore"))
            for item in root.findall(".//item"):
                raw_title = (item.findtext("title") or "").strip()
                if ":" in raw_title:
                    empresa, titulo = [p.strip() for p in raw_title.split(":", 1)]
                else:
                    empresa, titulo = "WeWorkRemotely", raw_title
                region = item.findtext("region") or "Worldwide"
                jobs.append({
                    "empresa": empresa,
                    "portal": "We Work Remotely",
                    "ats": "wwr",
                    "id": item.findtext("guid") or item.findtext("link") or "",
                    "titulo": titulo,
                    "ubicacion": region,
                    "workplace_type": "remote",
                    "url": (item.findtext("link") or "").strip(),
                    "descripcion": evaluador_ia.strip_html(item.findtext("description") or "")[:9000],
                })
        except Exception:
            continue
    return jobs


def consultar_jobicy() -> list[dict]:
    """API pública JSON de Jobicy (remoto tech global, por tema y LatAm)."""
    jobs: list[dict] = []
    seen: set[str] = set()
    urls = [
        "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops",
        "https://jobicy.com/api/v2/remote-jobs?count=50&tag=aws",
        "https://jobicy.com/api/v2/remote-jobs?count=50&tag=security",
        "https://jobicy.com/api/v2/remote-jobs?count=50&geo=latam",
        "https://jobicy.com/api/v2/remote-jobs?count=50&industry=dev",
    ]
    for url in urls:
        try:
            data = _get_json(url, timeout=10)
            for j in data.get("jobs", []):
                jid = str(j.get("id") or j.get("url") or "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                sal = "Sueldo por confirmar"
                if j.get("annualSalaryMin") and j.get("annualSalaryMax"):
                    sal = f"${j['annualSalaryMin']} - ${j['annualSalaryMax']} {j.get('salaryCurrency', 'USD')}/yr"
                jobs.append({
                    "empresa": j.get("companyName") or "Jobicy",
                    "portal": "Jobicy",
                    "ats": "jobicy",
                    "id": jid,
                    "titulo": j.get("jobTitle") or "",
                    "ubicacion": j.get("jobGeo") or "Anywhere",
                    "workplace_type": "remote",
                    "salario": sal,
                    "url": j.get("url") or "",
                    "descripcion": evaluador_ia.strip_html(j.get("jobDescription") or j.get("jobExcerpt") or "")[:9000],
                })
        except Exception:
            continue
    return jobs


# -------------------------------------------------------- Portales Remotos
def consultar_getonboard() -> list[dict]:
    """API pública de Get on Board con expand=["company"] y paginación — clave para Chile y LatAm."""
    pref = cargar_preferencias_usuario()
    queries = [
        "finops",
        "costos",
        "aws",
        "devops",
        "devsecops",
        "cloud security",
        "ciberseguridad",
        "secops",
        "cloud engineer",
        "infraestructura",
        "sre",
        "ai automation",
        "automatizacion",
    ]
    modalidad_pref = pref.get("modalidad") or "remoto_100"
    seen: set[str] = set()
    jobs: list[dict] = []
    for q in queries:
        for page in (1, 2):
            try:
                url = (
                    f"https://www.getonbrd.com/api/v0/search/jobs"
                    f"?query={urllib.parse.quote(q)}&per_page=50&page={page}&expand=%5B%22company%22%5D"
                )
                data = _get_json(url, timeout=10)
                items = data.get("data") or []
                for item in items:
                    jid = str(item.get("id") or "")
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    attr = item.get("attributes") or {}
                    if modalidad_pref == "remoto_100":
                        if not attr.get("remote"):
                            continue
                        modality = str(attr.get("remote_modality") or "")
                        if modality in ("no_remote", "hybrid", "temporarily_remote"):
                            continue
                    comp_obj = attr.get("company")
                    empresa = ""
                    if isinstance(comp_obj, dict):
                        empresa = ((comp_obj.get("data") or {}).get("attributes") or {}).get("name") or ""
                    if not empresa:
                        empresa = f"Confidencial GoB ({jid[:12]})"
                    zone = attr.get("remote_zone") or ", ".join(attr.get("countries") or []) or "LatAm / Chile"
                    min_s, max_s = attr.get("min_salary"), attr.get("max_salary")
                    sal = f"${min_s:,} - ${max_s:,} USD/mes" if min_s and max_s else "Sueldo por confirmar"
                    desc = evaluador_ia.strip_html(
                        f"{attr.get('description') or ''} {attr.get('functions') or ''} {attr.get('benefits') or ''} {attr.get('desirable') or ''}"
                    )
                    link = (item.get("links") or {}).get("public_url") or f"https://www.getonbrd.com/jobs/{jid}"
                    jobs.append({
                        "empresa": empresa,
                        "portal": "Get on Board",
                        "ats": "getonboard",
                        "id": jid,
                        "titulo": attr.get("title") or "",
                        "ubicacion": f"Remoto ({zone})",
                        "workplace_type": "remote",
                        "salario": sal,
                        "url": link,
                        "descripcion": desc[:9000],
                    })
                if len(items) < 50:
                    break
            except Exception:
                break
    return jobs


def consultar_remotive() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    urls = [
        "https://remotive.com/api/remote-jobs?category=devops&limit=60",
        "https://remotive.com/api/remote-jobs?category=software-dev&limit=60",
        "https://remotive.com/api/remote-jobs?search=finops",
        "https://remotive.com/api/remote-jobs?search=devsecops",
        "https://remotive.com/api/remote-jobs?search=aws",
        "https://remotive.com/api/remote-jobs?search=cloud+security",
    ]
    for url in urls:
        try:
            data = _get_json(url, timeout=10)
            for j in data.get("jobs", []):
                jid = str(j.get("id") or j.get("url") or "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                jobs.append({
                    "empresa": j.get("company_name") or "Remotive",
                    "portal": "Remotive",
                    "ats": "remotive",
                    "id": jid,
                    "titulo": j.get("title") or "",
                    "ubicacion": j.get("candidate_required_location") or "Worldwide",
                    "workplace_type": "remote",
                    "salario": j.get("salary") or "Sueldo por confirmar",
                    "url": j.get("url") or "",
                    "descripcion": evaluador_ia.strip_html(j.get("description") or "")[:9000],
                })
        except Exception:
            continue
    return jobs


def consultar_remoteok() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    urls = [
        "https://remoteok.com/api?tag=devops",
        "https://remoteok.com/api?tag=cloud",
        "https://remoteok.com/api?tag=security",
        "https://remoteok.com/api?tag=aws",
        "https://remoteok.com/api?tag=sysadmin",
    ]
    for url in urls:
        try:
            data = _get_json(url, timeout=10)
            for j in data[1:80]:
                if not isinstance(j, dict) or not j.get("position"):
                    continue
                jid = str(j.get("id") or j.get("url") or "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                sal = ""
                if j.get("salary_min") and j.get("salary_max"):
                    sal = f"${j['salary_min']:,} - ${j['salary_max']:,} USD/yr"
                jobs.append({
                    "empresa": j.get("company") or "RemoteOK",
                    "portal": "RemoteOK",
                    "ats": "remoteok",
                    "id": jid,
                    "titulo": j.get("position") or "",
                    "ubicacion": j.get("location") or "Worldwide",
                    "workplace_type": "remote",
                    "salario": sal or "Sueldo por confirmar",
                    "url": j.get("apply_url") or j.get("url") or "",
                    "descripcion": evaluador_ia.strip_html(j.get("description") or "")[:9000],
                })
        except Exception:
            continue
    return jobs


def consultar_himalayas() -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    queries = ["finops", "aws", "devsecops", "cloud security", "secops", "cloud engineer", "ai automation"]
    urls = [f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(q)}" for q in queries]
    urls.append("https://himalayas.app/jobs/api?limit=50")
    for url in urls:
        try:
            data = _get_json(url, timeout=10)
            for j in data.get("jobs", []):
                link = j.get("applicationLink") or ""
                jid = str(j.get("guid") or link)
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                locs = j.get("locationRestrictions") or []
                ubic = ", ".join(locs) if locs else "Worldwide"
                jobs.append({
                    "empresa": j.get("companyName") or "Himalayas",
                    "portal": "Himalayas",
                    "ats": "himalayas",
                    "id": jid,
                    "titulo": j.get("title") or "",
                    "ubicacion": ubic,
                    "workplace_type": "remote",
                    "salario": "Sueldo por confirmar",
                    "url": link,
                    "descripcion": evaluador_ia.strip_html(j.get("description") or "")[:9000],
                })
        except Exception:
            continue
    return jobs


def consultar_mercor() -> list[dict]:
    """API SSR (__NEXT_DATA__) de Mercor (work.mercor.com/explore) — filtra estrictamente:
    1) status == 'active', disableApplications == False, deletedAt is None
    2) Elegible desde Chile (CHL / LatAm / Remote global) sin restricción US/EU/UK/India-only."""
    jobs: list[dict] = []
    try:
        req = urllib.request.Request("https://work.mercor.com/explore", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
        if not m:
            return jobs
        data = json.loads(m.group(1))
        listings = []
        for q in (data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries") or []):
            st = (q.get("state") or {}).get("data")
            if isinstance(st, dict) and isinstance(st.get("listings"), list):
                listings = st["listings"]
                break

        for j in listings:
            if j.get("status") != "active" or j.get("disableApplications") or j.get("deletedAt"):
                continue
            rem_slots = j.get("remainingSlots")
            if rem_slots is not None and isinstance(rem_slots, int) and rem_slots <= 0:
                continue

            jid = str(j.get("listingId") or "").strip()
            title = str(j.get("title") or "").strip()
            if not jid or not title:
                continue
            desc = str(j.get("description") or "").strip()
            loc = str(j.get("location") or "Remote").strip()

            elig = [str(x).upper() for x in (j.get("eligibleLocation") or j.get("eligibleResidenceLocation") or []) if x]
            inelig = [str(x).upper() for x in (j.get("ineligibleLocation") or j.get("ineligibleResidenceLocation") or []) if x]
            if "CHL" in inelig or "CL" in inelig:
                continue
            if elig and not any(c in ("CHL", "CL", "LATAM", "WORLDWIDE", "GLOBAL") for c in elig):
                continue

            # Descartar si location o título indica otro país/acento/idioma ajeno sin Chile/LatAm
            loc_low = f"{title} {loc}".lower()
            if any(
                bad in loc_low
                for bad in (
                    "united states", "us only", "u.s.", "usa", "uk only", "united kingdom",
                    "british english", "australian english", "canadian english", "english (new zealand)",
                    "new zealand", "irish english", "scottish", "south african",
                    "canada", "europe", "apac", "india", "japan", "korea", "germany", "france",
                    "brazilian portuguese", "portuguese", "italian", "dutch", "french", "german",
                    "arabic", "hindi", "mandarin", "chinese", "thai", "ukrainian", "vietnamese",
                    "swahili", "greek", "hebrew", "indonesian", "bangla", "polish", "turkish",
                    "russian", "romanian", "finnish", "danish", "czech", "swedish", "norwegian",
                    "hungarian", "korean", "japanese", "tagalog", "filipino", "malay",
                    "armenian", "telugu", "tamil", "urdu", "marathi", "gujarati", "punjabi",
                    "kannada", "malayalam", "persian", "farsi", "catalan", "galician", "basque",
                    "spanish (spain)", "spanish (mexico)", "mexican spanish", "spanish (colombia)",
                    "spanish (peru)", "spanish (argentina)", "spanish (venezuela)", "spanish (us)",
                )
            ) and not any(ok in loc_low for ok in ("chile", "latin american spanish", "latam")) and "CHL" not in elig:
                continue

            # Filtrar por dominios relevantes al perfil de Felipe (Spanish/Bilingual, DevOps, Cloud, Security, Python, Software, Rubric)
            rel_txt = f"{title} {desc[:1200]}".lower()
            if not any(
                k in rel_txt
                for k in (
                    "spanish", "bilingual", "rubric", "prompt",
                    "devops", "platform", "cloud", "aws", "security", "cyber",
                    "soc", "sre", "site reliability", "software engineer", "python",
                    "mlops", "llm", "agent",
                )
            ):
                continue
            if any(
                irrel in title.lower()
                for irrel in (
                    "mechanical", "civil", "electrical", "chemical", "biolog", "medical",
                    "physician", "lawyer", "legal", "nursing", "accountant", "finance",
                    "investment banking", "davinci", "unreal engine", "fruitloops", "blender",
                    "illustrator", "premiere", "photoshop", "macbook", "voice actor",
                    "humanities", "arts & design", "consumer & lifestyle", "office & productivity",
                    "education & training", "business operations", "life, physical", "social science",
                    "pre-training & computer vision",
                )
            ):
                continue

            rmin = j.get("rateMin")
            rmax = j.get("rateMax")
            if rmin and rmax:
                sal = f"${int(rmin)} USD/hr" if rmin == rmax else f"${int(rmin)} - ${int(rmax)} USD/hr"
            elif j.get("hourlyPayRate"):
                sal = f"${j['hourlyPayRate']} USD/hr"
            else:
                sal = "Tarifa en USD/hr por proyecto"

            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            url = f"https://work.mercor.com/jobs/{jid}/{slug}"
            ubic_real = "100% Remoto (Chile elegible verificado: CHL)" if "CHL" in elig else f"Remoto ({loc})"

            jobs.append({
                "empresa": "Mercor",
                "portal": "Mercor API Oficial (Hourly USD)",
                "ats": "mercor",
                "es_ai_evaluator": True,
                "id": jid,
                "titulo": title,
                "ubicacion": ubic_real,
                "workplace_type": "remote",
                "salario": sal,
                "url": url,
                "descripcion": (
                    f"Mercor Remote Project: {title} ({sal}, {ubic_real}). "
                    f"Status: ACTIVE (remainingSlots={rem_slots}). "
                    f"{desc[:7000]}"
                ),
            })
    except Exception:
        pass
    return jobs


def consultar_micro1() -> list[dict]:
    """API oficial en vivo de micro1 (prod-api.micro1.ai/api/v1/job/portal) — solo vacantes EXPERT activas
    y elegibles desde Chile/LatAm."""
    jobs: list[dict] = []
    try:
        all_items: list[dict] = []
        for page in (1, 2, 3):
            req = urllib.request.Request(
                f"https://prod-api.micro1.ai/api/v1/job/portal?page={page}&limit=100&keyword=",
                data=json.dumps({"action": "get_all_jobs", "filters": {"type": ["EXPERT"]}}).encode("utf-8"),
                headers={**HEADERS, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                batch = data.get("data") or []
                all_items.extend(batch)
                if len(batch) < 100:
                    break

        for j in all_items:
            jid = str(j.get("job_id") or "").strip()
            title = str(j.get("job_name") or "").strip()
            apply_url = str(j.get("apply_url") or "").split("?")[0].strip()
            if not jid or not title or not apply_url:
                continue

            low_t = title.lower()
            # Descartar vacantes restringidas a otro país o idioma ajeno
            if any(
                bad in low_t
                for bad in (
                    "us only", "us-based", "us based", "us/can", "u.s.", "united states",
                    "canada", "united kingdom", "great britain", "ireland", "germany",
                    "france", "australia", "japan", "south korea", "india", "philippines",
                    "south africa", "nigeria", "egypt", "brazil", "portugal", "belgium",
                    "switzerland", "spain)", "mexico)", "mexico only", "arabic", "french",
                    "portuguese", "german", "japanese", "korean", "phd", "physician",
                    "doctor", "oncologist", "lawyer", "legal", "veterans",
                )
            ):
                continue

            skills = [str(s).strip() for s in (j.get("skills") or []) if str(s).strip()]
            # Exigir keyword relevante en el TÍTULO del puesto en micro1 (evitar médicos/abogados con skill genérico "data")
            if not any(
                k in low_t
                for k in (
                    "spanish", "latam", "bilingual", "language", "linguistic", "prompt",
                    "evaluator", "generalist", "python", "devops", "cloud", "aws", "security",
                    "cyber", "jailbreak", "red teaming", "software", "backend", "full-stack",
                    "data analyst", "data scientist", "data engineer", "sql", "linux",
                    "ai trainer", "llm", "machine learning", "simulation",
                )
            ):
                continue
            if any(
                irrel in low_t
                for irrel in (
                    "photographer", "sony", "robotics", "drone", "maritime", "imagery",
                    "federal", "civilian intelligence", "national security", "nuclear",
                    "aerodynamics", "mechanical", "biology", "chemistry", "physics",
                    "earth &", "social work", "travel operations", "shopper", "category manager",
                    "retail", "food delivery", "video data entry", "wearing", "cardiolog",
                    "medical", "clinical", "nurse", "radiolog", "neurolog", "pathology",
                    "life science", "life sciences", "physical science", "physical sciences",
                    "mathematics", "math", "statistics", "kaggle", "biomedical", "genomics",
                    "biotech", "neuroscience", "materials science",
                )
            ):
                continue
            rate = j.get("ideal_hourly_rate") or {}
            rmin, rmax = rate.get("min"), rate.get("max")
            if rmin and rmax:
                sal = f"${rmin} - ${rmax} USD/hr"
            else:
                sal = "Tarifa en USD/hr"

            jobs.append({
                "empresa": "micro1",
                "portal": "micro1 API Oficial (Hourly USD)",
                "ats": "micro1",
                "es_ai_evaluator": True,
                "id": jid,
                "titulo": title,
                "ubicacion": "Remoto (Chile / LatAm)",
                "workplace_type": "remote",
                "salario": sal,
                "url": apply_url,
                "descripcion": (
                    f"micro1 Remote Expert Role: {title} ({sal}). "
                    f"Required skills: {', '.join(skills)}. "
                    f"Posted: {j.get('date_posted') or 'Active'}."
                ),
            })
    except Exception:
        pass
    return jobs


def consultar_plataformas_ai_evaluator() -> list[dict]:
    """Combina las APIs en vivo de Mercor y micro1 con los portales permanentes verificados
    (DataAnnotation, Outlier, Mindrift, Alignerr, Turing)."""
    permanentes = [
        {
            "empresa": "DataAnnotation",
            "portal": "DataAnnotation (Hourly USD)",
            "ats": "dataannotation",
            "es_ai_evaluator": True,
            "id": "dataannotation-bilingual-python",
            "titulo": "AI Evaluator & Prompt/Code Trainer (Bilingual Spanish-English & Python Track)",
            "ubicacion": "100% Remoto Global (Chile / LatAm)",
            "workplace_type": "remote",
            "salario": "$20 - $40+ USD/hora",
            "url": "https://www.dataannotation.tech/",
            "descripcion": "Side-by-side prompt evaluation, RLHF model comparison, bilingual English-Spanish translation and Python coding AI trainer.",
        },
        {
            "empresa": "Outlier.ai (Scale AI)",
            "portal": "Outlier (Hourly USD)",
            "ats": "outlier",
            "es_ai_evaluator": True,
            "id": "outlier-latam-trainer",
            "titulo": "AI Trainer — Bilingual Spanish (Chile/LatAm) & Coding/Prompt Evaluator",
            "ubicacion": "100% Remoto (Chile / LatAm)",
            "workplace_type": "remote",
            "salario": "$15 - $35 USD/hora",
            "url": "https://app.outlier.ai/en/expert/jobs",
            "descripcion": "RLHF AI Trainer for Spanish (Chile/LatAm) prompt evaluation, side-by-side response comparison, Python, SQL and Bash.",
        },
        {
            "empresa": "Mindrift (by Toloka AI)",
            "portal": "Mindrift (Hourly USD)",
            "ats": "mindrift",
            "es_ai_evaluator": True,
            "id": "mindrift-ai-tutor",
            "titulo": "Freelance AI Tutor — Prompt Editor & Technical/Bilingual Evaluator",
            "ubicacion": "100% Remoto Global (Chile / LatAm)",
            "workplace_type": "remote",
            "salario": "$15 - $40 USD/hora",
            "url": "https://mindrift.ai/apply",
            "descripcion": "AI Tutor and Prompt Editor evaluating LLM responses against rubrics, fact-checking, bilingual EN/ES and IT domain expertise.",
        },
        {
            "empresa": "Turing",
            "portal": "Turing (Hourly USD)",
            "ats": "turing-llm",
            "es_ai_evaluator": True,
            "id": "turing-llm-trainer",
            "titulo": "LLM Trainer & RLHF Evaluator (Instruction Quality, Bilingual & Python Track)",
            "ubicacion": "100% Remoto (Chile / LatAm)",
            "workplace_type": "remote",
            "salario": "$20 - $45 USD/hora",
            "url": "https://www.turing.com/jobs",
            "descripcion": "LLM Trainer and RLHF Evaluator focusing on instruction following quality, multi-turn prompt evaluation, hallucination detection and Python.",
        },
        {
            "empresa": "Alignerr (by Labelbox)",
            "portal": "Alignerr (Hourly USD)",
            "ats": "alignerr",
            "es_ai_evaluator": True,
            "id": "alignerr-spanish-coding-trainer",
            "titulo": "AI Trainer — Bilingual Spanish Evaluator & Coding/Cloud Domain Expert",
            "ubicacion": "100% Remoto Global (Chile / LatAm)",
            "workplace_type": "remote",
            "salario": "$20 - $60+ USD/hora",
            "url": "https://www.alignerr.com/jobs",
            "descripcion": "Labelbox Alignerr RLHF AI Evaluator and Trainer for bilingual Spanish-English localization, prompt engineering, Python, DevOps and cloud.",
        },
    ]
    return consultar_mercor() + consultar_micro1() + permanentes


# -------------------------------------------------------- Pre-filtro estricto (solo hechos inequívocos)
TERMS_EXCLUSION_SOLO_TITULO = {
    "presencial", "híbrido", "hibrido", "us only", "eu only",
    "manager", "director", "scrum master", "project manager",
    "staff", "principal", "lead", "vp", "head of", "chief", "intern",
}
MAPA_SINONIMOS_REGION = {
    "españa": ("spain", "españa"),
    "spain": ("spain", "españa"),
    "chile": ("chile", "santiago"),
    "latam": ("latam", "latin america", "south america", "americas"),
}


def pasa_filtros_duros(job: dict) -> tuple[bool, str]:
    """Filtra únicamente descartes deterministas inequívocos (0 tokens).
    Casos grises o semánticos pasan al Triaje IA en lote para no matar falsos negativos."""
    pref = cargar_preferencias_usuario()
    modalidad_pref = pref.get("modalidad") or "remoto_100"
    solo_ic = pref.get("solo_ic", True)
    sen_obj = (pref.get("seniority_objetivo") or "mid").lower()
    pais = (pref.get("pais_residencia") or "").strip().lower()
    regiones = [str(r).strip().lower() for r in (pref.get("regiones_aceptadas") or []) if str(r).strip()]
    exclusiones = [str(x).strip().lower() for x in (pref.get("exclusiones") or []) if str(x).strip()]

    # Expandir sinónimos de regiones aceptadas (ej. España -> spain)
    regiones_exp: set[str] = set()
    for r in regiones:
        if r not in ("remote", "remoto", "worldwide", "global"):
            regiones_exp.add(r)
            for syn in MAPA_SINONIMOS_REGION.get(r, ()):
                regiones_exp.add(syn)
    if pais:
        regiones_exp.add(pais)
        for syn in MAPA_SINONIMOS_REGION.get(pais, ()):
            regiones_exp.add(syn)

    titulo = job.get("titulo") or ""
    ubic = job.get("ubicacion") or ""
    wp = (job.get("workplace_type") or "").lower()
    desc_head = (job.get("descripcion") or "")[:2500]

    # 0. Descartar posibles Ghost Jobs (>45 días sin actualización) y roles claramente no técnicos
    f_pub = job.get("fecha_publicacion")
    if f_pub:
        try:
            dias_abierta = (datetime.date.today() - datetime.date.fromisoformat(str(f_pub)[:10])).days
            if dias_abierta > 45:
                return False, f"posible ghost job ({dias_abierta} días abierta)"
        except Exception:
            pass
    if RE_TITULO_NO_TECNICO.search(titulo) and not RE_TITULO_TECNICO.search(titulo):
        return False, "rol no técnico"

    # 1. Descartar Managers / Directors / Staff / Principal SOLO EN TÍTULO y exceso de años/Senior fuera de nicho
    if solo_ic and sen_obj in ("junior", "mid") and RE_EXCLUIR_TITULO.search(titulo):
        return False, "rol de gestión o seniority Staff/Principal/Lead en título"
    if solo_ic and re.search(r"\b(manager|director|vp\b|vice president|head of|chief)\b", titulo, re.I):
        return False, "rol de gestión/Manager (perfil configurado como IC)"
    if sen_obj in ("junior", "mid"):
        if re.search(
            r"\b(?:[5-9]|1[0-9])\s*\+?\s*(?:years?|yrs?|años)\b|"
            r"\b(?:at least|minimum(?: of)?|mínimo(?: de)?)\s+(?:[5-9]|1[0-9])\s*(?:years?|yrs?|años)\b",
            f"{titulo} {desc_head}",
            re.I,
        ):
            return False, "exige 5+ años de experiencia (exceso de seniority)"
        if RE_SENIOR_ALTO.search(titulo) and not re.search(r"\b(finops|cost|cloud financial)\b", titulo, re.I):
            return False, "título Senior/Sr. fuera de nicho FinOps (exceso de seniority)"

    # 1b. Exclusiones personalizadas del usuario:
    #     - Términos de seniority/gestión se buscan EXCLUSIVAMENTE en `titulo` (evita matar 'staff augmentation', 'team lead', 'objetivo principal').
    #     - Términos de disciplinas científicas se buscan en `titulo`.
    for ex in exclusiones:
        if len(ex) < 3 or ex in ("presencial", "híbrido", "hibrido", "us only", "eu only"):
            continue
        target_txt = titulo if ex in TERMS_EXCLUSION_SOLO_TITULO or job.get("es_ai_evaluator") else titulo
        if re.search(r"\b" + re.escape(ex) + r"\b", target_txt, re.I):
            return False, f"coincide con exclusión no negociable '{ex}' en título"

    # 2. Modalidad aceptada
    if modalidad_pref == "remoto_100":
        if wp in ("onsite", "on-site", "hybrid") or RE_HIBRIDO_PRESENCIAL.search(ubic):
            return False, f"no es 100% remoto ({ubic or wp})"

    # 2b. Si la propia UBICACIÓN o el TÍTULO declaran restricción dura a otro país ("USA Only", "Remote (IN)", "Remote (DE)", "Eligible in Bulgaria"),
    #     descartar directo sin dejar que la palabra "worldwide" en el marketing de la empresa lo salve.
    if re.search(
        r"(\b(us|usa|u\.s\.|uk|eu|canada|india|apac|emea|germany|australia)\s+only\b|"
        r"remote\s*\(\s*(?!cl\b|chl\b|es\b)[a-z]{2}\s*\)|"
        r"remote\s*\(\s*(usa|united states|united kingdom|canada|india|germany|france|australia|brazil|mexico|argentina|colombia)\s*\)|"
        r"eligible in (bulgaria|romania|poland|india|uk|ireland|canada|us|usa|germany|portugal))",
        f"{titulo} {ubic}",
        re.I,
    ):
        if not re.search(r"\b(chile|latam|latin america)\b", ubic, re.I):
            return False, f"ubicación restringida explícitamente ({ubic})"

    # 3. Si la ubicación estructurada dice explícitamente Worldwide / Anywhere / LatAm / Chile / Región aceptada,
    #    NO descartar por menciones secundarias a EE.UU. en el cuerpo (salvo "US citizens only" / "security clearance")
    ubic_pura_global = bool(
        re.search(r"\b(chile|santiago|latam|latin america|south america|worldwide|anywhere|global|americas|amer\b)\b", ubic, re.I)
        or any(re.search(r"\b" + re.escape(rx) + r"\b", ubic, re.I) for rx in regiones_exp if len(rx) >= 3)
    )
    es_ubic_global_o_aceptada = ubic_pura_global or ubic.strip().lower() in ("remote", "remoto", "100% remote", "remote worldwide", "remoto (remote)")
    if re.search(r"\b(us citizens? only|usc\s+(and|or|&|/)\s*gc|active\s+(?:ts/sci|secret|top secret)\s+clearance|must reside in the u\.?s)\b", f"{titulo} {desc_head[:1500]}", re.I):
        if not re.search(r"\b(chile|latam|latin america)\b", f"{ubic} {desc_head[:1200]}", re.I):
            return False, f"exige ciudadanía/residencia estricta EE.UU. ({ubic})"

    if not ubic_pura_global:
        # Si ubic lista países específicos separados por coma y ninguno es Chile/LatAm/Global/Región aceptada, descartar
        if "," in ubic and "remote" not in ubic.lower() and "remoto" not in ubic.lower():
            return False, f"lista de países sin Chile/LatAm ({ubic[:50]})"
        if RE_PAIS_UNICO_EXCLUIDO.match(ubic) or RE_SOLO_OTRO_PAIS.search(f"{titulo} {ubic}") or (not es_ubic_global_o_aceptada and RE_SOLO_OTRO_PAIS.search(desc_head)):
            menciona_aceptada = bool(re.search(r"\b(chile|latam|latin america)\b", f"{ubic} {desc_head[:1200]}", re.I)) or any(
                re.search(r"\b" + re.escape(rx) + r"\b", ubic, re.I) for rx in regiones_exp if len(rx) >= 3
            )
            if not menciona_aceptada:
                return False, f"restringido fuera de tu región ({ubic})"

    # 4. Si exige 100% remoto y la ubicación nombra una ciudad fija sin remoto
    if modalidad_pref == "remoto_100":
        if RE_CIUDAD_SIN_REMOTO.search(ubic) and not es_ubic_global_o_aceptada and "remote" not in ubic.lower():
            return False, f"ubicación fija fuera de tu región ({ubic})"

        if wp != "remote" and not es_ubic_global_o_aceptada and "remote" not in ubic.lower():
            if not re.search(r"\b(100%\s*remote|fully remote|work from anywhere|telecommut\w*|remote\s*[-–]?\s*(latam|global|worldwide|chile|spain|americas))\b", desc_head, re.I):
                return False, f"sin señal de 100% remoto ({ubic or 'sin ubicación'})"

    return True, "ok"


PRIORIDAD_FUENTE = {
    "finops_org": 0,
    "getonboard": 1,
    "teamtailor": 1,
    "ashby": 2,
    "greenhouse": 2,
    "lever": 2,
    "smartrecruiters": 2,
    "workable": 2,
    "breezy": 2,
    "recruitee": 2,
    "rippling": 2,
    "pinpoint": 2,
    "wazuh": 2,
    "wwr": 3,
    "remotive": 3,
    "remoteok": 3,
    "jobicy": 4,
    "workingnomads": 4,
    "himalayas": 4,
}


def filtrar_trabajos(jobs: list[dict], keywords_filtro: list[str] | None = None) -> tuple[list[dict], dict]:
    if not keywords_filtro:
        keywords_filtro = ALL_KEYWORDS

    stats = {"descartadas_ubicacion_o_rol": 0, "sin_keywords": 0, "duplicadas_cross_portal": 0}
    coincidencias = []
    seen_urls: set[str] = set()
    seen_tuplas_lote: set[tuple[str, str]] = set()

    # Ordenar primero por autoridad de la fuente para que ATS Oficial / GetOnBoard / FinOps.org
    # ganen frente a agregadores cuando la misma vacante aparece en ambos
    jobs_sorted = sorted(jobs, key=lambda x: PRIORIDAD_FUENTE.get(str(x.get("ats") or ""), 5))

    for j in jobs_sorted:
        url = (j.get("url") or "").strip()
        u_clean = limpiar_url_dedup(url)
        if not url or u_clean in seen_urls:
            continue
        seen_urls.add(u_clean)

        emp_slug = normalizar_slug(j.get("empresa") or "")
        tit_slug = normalizar_slug(j.get("titulo") or "")
        if emp_slug and tit_slug and (emp_slug, tit_slug) in seen_tuplas_lote:
            stats["duplicadas_cross_portal"] += 1
            continue

        ok, motivo = pasa_filtros_duros(j)
        if not ok:
            stats["descartadas_ubicacion_o_rol"] += 1
            continue

        kws_job = TITULO_AI_EVAL + ["spanish", "bilingual", "python", "devops", "cloud", "security", "rubric"] if j.get("es_ai_evaluator") else keywords_filtro
        score, t_hits, s_hits = pre_puntaje(j, kws_job)
        titulo = j.get("titulo") or ""
        es_tec = bool(RE_TITULO_TECNICO.search(titulo)) or bool(j.get("es_ai_evaluator"))

        # Exigir señal real: match en título, o título técnico + al menos 2 tecnologías del stack en descripción
        if not t_hits and not (es_tec and len(s_hits) >= 2):
            stats["sin_keywords"] += 1
            continue

        if emp_slug and tit_slug:
            seen_tuplas_lote.add((emp_slug, tit_slug))
        j["pre_score"] = score
        j["keywords_matched"] = list(dict.fromkeys(t_hits + s_hits))[:10]
        coincidencias.append(j)

    coincidencias.sort(key=lambda x: x.get("pre_score", 0), reverse=True)
    return coincidencias, stats


def verificar_disponibilidad_url(job: dict) -> tuple[bool, str]:
    """Verifica en vivo (HTTP / API) que la oferta siga disponible y aceptando postulaciones
    antes de evaluarla con IA o crear su ficha."""
    url = str(job.get("url") or "").strip()
    if not url.startswith("http"):
        return False, "URL inválida"

    ats = (job.get("ats") or "").lower()
    if ats in ("greenhouse", "ashby", "lever", "mercor", "micro1", "getonboard", "teamtailor", "breezy", "smartrecruiters", "workable", "finops_org", "wazuh"):
        return True, "API oficial activa"

    re_cerrada = re.compile(
        r"(no longer accepting|no longer available|job (not found|closed|expired|has been filled)|"
        r"position has been filled|this (job|position|listing|opportunity) is (closed|no longer)|"
        r"applications? (are |is )?closed|esta oferta (ha finalizado|ya no está disponible|fue cerrada)|"
        r"el aviso ya no se encuentra activo)",
        re.I,
    )
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            if resp.status >= 400:
                return False, f"HTTP {resp.status}"
            body = resp.read(32000).decode("utf-8", errors="ignore")
            m = re_cerrada.search(body)
            if m:
                return False, f"Aviso cerrado ({m.group(0)})"
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return False, f"HTTP {e.code} (No encontrada)"
        return True, f"HTTP {e.code}"
    except Exception as e:
        return False, f"Error de conexión ({str(e)[:40]})"


def limpiar_url_dedup(url: str) -> str:
    if not url:
        return ""
    try:
        u = urllib.parse.urlparse(url)
        return f"{u.netloc.lower()}{u.path.rstrip('/')}"
    except Exception:
        return url.strip().lower().rstrip("/")


def normalizar_slug(texto: str) -> str:
    t = (texto or "").lower()
    return re.sub(r"[^a-z0-9]", "", t)


def cargar_seen_cache() -> dict[str, dict]:
    """Carga caché local de URLs ya evaluadas y descartadas por IA (TTL de SEEN_TTL_DIAS)
    para que nunca vuelvan a atascar el cupo --max-eval en corridas futuras."""
    if not SEEN_CACHE_PATH.is_file():
        return {}
    try:
        raw = json.loads(SEEN_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        hoy_d = datetime.date.today()
        vigentes = {}
        for k, v in raw.items():
            if not isinstance(v, dict):
                continue
            try:
                f = datetime.date.fromisoformat(str(v.get("fecha") or ""))
                if (hoy_d - f).days <= SEEN_TTL_DIAS:
                    vigentes[k] = v
            except Exception:
                vigentes[k] = v
        return vigentes
    except Exception:
        return {}


def guardar_seen_cache(cache: dict[str, dict]) -> None:
    try:
        SEEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEEN_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def registrar_seen_url(cache: dict[str, dict], job: dict, motivo: str, score: int = 0) -> None:
    u_clean = limpiar_url_dedup(str(job.get("url") or ""))
    if not u_clean:
        return
    cache[u_clean] = {
        "fecha": datetime.date.today().isoformat(),
        "empresa": job.get("empresa") or "",
        "titulo": job.get("titulo") or "",
        "score": score,
        "motivo": motivo[:140],
    }


def cargar_indice_existentes() -> tuple[set[str], set[tuple[str, str]]]:
    """Carga todas las ofertas existentes del repo en memoria para deduplicar
    por URL limpia y por tupla (empresa, cargo) a 0 tokens y sin subprocesos."""
    urls = set()
    tuplas = set()
    try:
        import estado
        ofertas, _ = estado.cargar_ofertas()
        for o in ofertas:
            u = str(o.get("url") or "").strip()
            if u:
                urls.add(limpiar_url_dedup(u))
                urls.add(u.lower().rstrip("/"))
            emp = normalizar_slug(str(o.get("empresa") or ""))
            car = normalizar_slug(str(o.get("cargo") or ""))
            if emp and car and emp != "empresagetonboard":
                tuplas.add((emp, car))
    except Exception:
        pass
    return urls, tuplas


def ya_existe_oferta(
    job: dict,
    urls_existentes: set[str],
    tuplas_existentes: set[tuple[str, str]],
    seen_cache: dict[str, dict] | None = None,
) -> tuple[bool, str]:
    url = str(job.get("url") or "").strip()
    u_clean = limpiar_url_dedup(url)
    if u_clean in urls_existentes or url.lower().rstrip("/") in urls_existentes:
        return True, "URL ya registrada"

    if seen_cache and u_clean in seen_cache:
        return True, f"evaluada y descartada recientemente ({seen_cache[u_clean].get('motivo', 'cache')})"

    emp = normalizar_slug(str(job.get("empresa") or ""))
    car = normalizar_slug(str(job.get("titulo") or ""))
    if emp and car and (emp, car) in tuplas_existentes:
        return True, f"vacante de '{job.get('empresa')}' para '{job.get('titulo')}' ya registrada previamente"

    return False, ""


def ya_existe(url: str) -> bool:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "estado.py"), "existe", url],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return res.returncode == 0


def ejecutar_doctor(catalogo: dict) -> None:
    """Verifica en vivo qué empresas del catálogo responden OK y cuáles dan 404/0 vacantes."""
    print("🩺 Ejecutando diagnóstico de salud sobre perfil/empresas-target.yaml...\n")
    checks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as ex:
        futs = {}
        for ats, fn in [
            ("greenhouse", consultar_greenhouse),
            ("ashby", consultar_ashby),
            ("lever", consultar_lever),
            ("teamtailor", consultar_teamtailor),
            ("breezy", consultar_breezy),
            ("smartrecruiters", consultar_smartrecruiters),
            ("workable", consultar_workable),
            ("recruitee", consultar_recruitee),
            ("rippling", consultar_rippling),
            ("pinpoint", consultar_pinpoint),
        ]:
            for slug in catalogo.get(ats, []):
                futs[ex.submit(fn, slug)] = (ats, slug)
        for fut, (ats, slug) in futs.items():
            res = fut.result() or []
            checks.append((ats, slug, len(res)))
    ok = [c for c in checks if c[2] > 0]
    ceros = [c for c in checks if c[2] == 0]
    print(f"✓ Tableros activos con vacantes: {len(ok)}/{len(checks)} ({sum(x[2] for x in ok)} vacantes totales)")
    if _ATS_ERRORS:
        print(f"✗ Errores HTTP/404 ({len(_ATS_ERRORS)}): {', '.join(sorted(_ATS_ERRORS))}")
    if ceros:
        print(f"ℹ Con 0 vacantes hoy ({len(ceros)}): {', '.join(f'{a}:{s}' for a, s, _ in ceros)}")


def armar_cola_balanceada(nuevas: list[dict], foco: str, max_pool: int) -> list[dict]:
    """Separa el carril principal (FinOps / Cloud / DevSecOps / SecOps / AI Auto) del carril
    secundario (AI Evaluator por horas) para que Mercor/micro1 jamás desplacen a las ofertas core."""
    if foco.lower() in ("ai-evaluator", "rlhf"):
        return nuevas[:max_pool]
    core = [j for j in nuevas if not j.get("es_ai_evaluator")]
    ai_eval = [j for j in nuevas if j.get("es_ai_evaluator")]
    cupo_ai_eval = min(len(ai_eval), max(3, int(max_pool * 0.15)))
    cupo_core = max_pool - cupo_ai_eval
    seleccion = core[:cupo_core] + ai_eval[:cupo_ai_eval]
    if len(seleccion) < max_pool and len(core) > cupo_core:
        seleccion.extend(core[cupo_core : cupo_core + (max_pool - len(seleccion))])
    return seleccion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buscar", action="store_true", help="Buscar vacantes en ATS + portales remotos.")
    parser.add_argument(
        "--foco",
        type=str,
        default="todos",
        help="Slug del área/foco configurado en tu perfil (o 'todos')",
    )
    parser.add_argument("--agregar", nargs=2, metavar=("ATS", "SLUG"))
    parser.add_argument("--descubrir-url", type=str)
    parser.add_argument("--empresa", type=str)
    parser.add_argument("--solo-ats", action="store_true", help="Omitir portales remotos públicos.")
    parser.add_argument("--crear-ofertas", action="store_true", help="Evaluar con IA y crear fichas en ofertas/<hoy>/.")
    parser.add_argument("--evaluar-ia", action="store_true", help="Evaluar con IA sin crear fichas (por defecto --buscar gasta 0 tokens).")
    parser.add_argument("--guardar-descartadas", action="store_true", help="Guardar también ofertas que la IA descarte.")
    parser.add_argument("--sin-triaje", action="store_true", help="Omitir pre-triaje semántico en lote con IA.")
    parser.add_argument("--limpiar-cache", action="store_true", help="Limpiar caché local de URLs descartadas (.seen-urls.json) antes de buscar.")
    parser.add_argument("--doctor", action="store_true", help="Diagnosticar salud de todos los slugs en empresas-target.yaml.")
    parser.add_argument("--max-eval", type=int, default=20, help="Tope de ofertas nuevas a evaluar en profundidad por corrida.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.agregar:
        agregar_empresa(args.agregar[0], args.agregar[1])
        return
    if args.descubrir_url:
        descubrir_desde_url(args.descubrir_url)
        return

    catalogo = cargar_catalogo()
    if args.doctor:
        ejecutar_doctor(catalogo)
        return

    if args.limpiar_cache and SEEN_CACHE_PATH.is_file():
        SEEN_CACHE_PATH.unlink(missing_ok=True)
        print("🧹 Caché local de URLs descartadas (.seen-urls.json) limpiado.")

    kws = obtener_keywords_dinamicas(args.foco)

    todos_los_trabajos: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        tasks = []
        solo_ai_eval = args.foco.lower() in ("ai-evaluator", "rlhf")
        if solo_ai_eval:
            tasks.append(ex.submit(consultar_plataformas_ai_evaluator))
            for slug in ("toloka", "turing", "labelbox", "invisible"):
                tasks.append(ex.submit(consultar_greenhouse, slug))
        else:
            for slug in catalogo.get("ashby", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_ashby, slug))
            for slug in catalogo.get("greenhouse", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_greenhouse, slug))
            for slug in catalogo.get("lever", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_lever, slug))
            for slug in catalogo.get("teamtailor", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_teamtailor, slug))
            for slug in catalogo.get("breezy", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_breezy, slug))
            for slug in catalogo.get("smartrecruiters", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_smartrecruiters, slug))
            for slug in catalogo.get("workable", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_workable, slug))
            for slug in catalogo.get("recruitee", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_recruitee, slug))
            for slug in catalogo.get("rippling", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_rippling, slug))
            for slug in catalogo.get("pinpoint", []):
                if not args.empresa or args.empresa.lower() == slug.lower():
                    tasks.append(ex.submit(consultar_pinpoint, slug))
            if not args.solo_ats and not args.empresa:
                tasks.append(ex.submit(consultar_finops_foundation))
                tasks.append(ex.submit(consultar_wazuh))
                tasks.append(ex.submit(consultar_getonboard))
                tasks.append(ex.submit(consultar_wwr))
                tasks.append(ex.submit(consultar_jobicy))
                tasks.append(ex.submit(consultar_remotive))
                tasks.append(ex.submit(consultar_remoteok))
                tasks.append(ex.submit(consultar_himalayas))
                tasks.append(ex.submit(consultar_workingnomads))
                if args.foco.lower() in ("", "todos"):
                    tasks.append(ex.submit(consultar_plataformas_ai_evaluator))

        for fut in concurrent.futures.as_completed(tasks):
            try:
                res = fut.result()
                if res:
                    todos_los_trabajos.extend(res)
            except Exception:
                pass

    # Etapa 1 y 2: Filtrar hechos duros inequívocos + deduplicación cross-portal
    coincidencias, stats = filtrar_trabajos(todos_los_trabajos, kws)
    urls_existentes, tuplas_existentes = cargar_indice_existentes()
    seen_cache = cargar_seen_cache()

    # Deduplicar ANTES de pasar al triaje y evaluación IA
    nuevas_candidatas = []
    omitidas_previas = 0
    for c in coincidencias:
        existe, _ = ya_existe_oferta(c, urls_existentes, tuplas_existentes, seen_cache)
        if existe:
            omitidas_previas += 1
        else:
            nuevas_candidatas.append(c)

    usar_ia = bool(args.crear_ofertas or args.evaluar_ia)
    prov = evaluador_ia.detectar_proveedor_ia(rol="buscador-ofertas")

    if args.json:
        print(json.dumps(nuevas_candidatas, indent=2, ensure_ascii=False))
        return

    print(f"\n=======================================================")
    print(f"🔎 RASTREO MULTI-FUENTE ({len(todos_los_trabajos)} vacantes descargadas)")
    if _ATS_ERRORS:
        print(f"⚠️  Fuentes con aviso/404 ({len(_ATS_ERRORS)}): {', '.join(_ATS_ERRORS[:5])}")
    print(f"🚫 Filtradas por modalidad/ubicación/título: {stats['descartadas_ubicacion_o_rol']} · Cross-portal dups: {stats['duplicadas_cross_portal']}")
    print(f"🎯 Candidatas válidas: {len(coincidencias)} ({omitidas_previas} ya vistas/registradas → {len(nuevas_candidatas)} NUEVAS reales)")
    if usar_ia:
        print(f"🤖 Evaluador activo: {prov['provider']}/{prov['model']} + Triaje en Lote + Neo4j GraphRAG")
    else:
        print(f"💡 Modo vista previa (0 tokens) — agrega --crear-ofertas o --evaluar-ia para puntuar con IA")
    print(f"=======================================================\n", flush=True)

    # Priorizar ofertas más recientes (frescura < 72h primero) antes de armar el pool
    nuevas_candidatas.sort(key=lambda j: str(j.get("fecha_publicacion") or "0000-00-00"), reverse=True)

    max_eval = args.max_eval if args.max_eval and args.max_eval > 0 else len(nuevas_candidatas)
    pool_size = min(len(nuevas_candidatas), max(45, max_eval * 3))
    pool = armar_cola_balanceada(nuevas_candidatas, args.foco, pool_size)

    # Etapa 3: Triaje semántico en lote con IA (piensa sobre un pool 3x más grande antes de evaluar a fondo)
    cola_final = pool[:max_eval]
    if usar_ia and not args.sin_triaje and prov["provider"] != "heuristica" and len(pool) > max_eval:
        print(f"🧠 Ejecutando Triaje IA en lote sobre {len(pool)} candidatas nuevas para elegir las top {max_eval}...", flush=True)
        triaje = evaluador_ia.triaje_lote_ofertas(pool, max_seleccion=max_eval, rol="buscador-ofertas")
        for d in triaje.get("descartadas") or []:
            registrar_seen_url(seen_cache, pool[d["idx"]], f"Triaje IA: {d['motivo']}", score=0)
        guardar_seen_cache(seen_cache)
        cola_final = [pool[i] for i in triaje.get("seleccionadas") or []]
        print(f"   ✓ Triaje completado: {len(cola_final)} seleccionadas para evaluación profunda · {len(triaje.get('descartadas') or [])} descartadas en caché.\n", flush=True)

    hoy = datetime.date.today().strftime("%Y-%m-%d")
    evaluadas = 0
    creadas = 0

    for c in cola_final:
        if args.max_eval and evaluadas >= args.max_eval:
            break
        ok_vivo, motivo_vivo = verificar_disponibilidad_url(c)
        if not ok_vivo:
            print(f"⏭ Omitida por no estar disponible ({motivo_vivo}): {c['empresa']} — {c['titulo']} ({c['url']})")
            registrar_seen_url(seen_cache, c, f"No disponible ({motivo_vivo})", score=0)
            continue

        evaluadas += 1
        if not usar_ia:
            kws_txt = ", ".join((c.get("keywords_matched") or [])[:6])
            print(f"• [pre {c.get('pre_score', 0):>2}/100] {c['empresa']} — {c['titulo']}")
            print(f"   Portal: {c['portal']} | Ubicación: {c.get('ubicacion')} | Match: {kws_txt}")
            print(f"   Link: {c['url']}\n", flush=True)
            continue

        ev = evaluador_ia.evaluar_oferta(c, rol="buscador-ofertas")
        icono = {"alta": "🔥", "media": "✅", "stretch": "⚠️", "descartada": "❌"}.get(ev.get("prioridad"), "•")
        print(f"{icono} [{ev.get('match_score', 0)}/100 · {ev.get('prioridad')} · pre {c.get('pre_score', 0)}] {c['empresa']} — {c['titulo']}")
        print(f"   Portal: {c['portal']} | Ángulo: {ev.get('angulo')} | Modalidad: {ev.get('modalidad_real')} | Salario: {ev.get('salario_detectado')}")
        print(f"   Resumen: {ev.get('resumen_ia')}")
        if ev.get("logros_grafo_match"):
            print(f"   Grafo Match: {', '.join(ev['logros_grafo_match'][:2])}")

        if args.crear_ofertas:
            if ev.get("prioridad") == "descartada" and not args.guardar_descartadas:
                mot_d = str(ev.get("motivo_descarte") or "Descartada por IA")
                registrar_seen_url(seen_cache, c, mot_d, score=int(ev.get("match_score") or 0))
                guardar_seen_cache(seen_cache)
                print(f"   ↳ Omitida y guardada en caché .seen-urls ({mot_d})\n", flush=True)
                continue
            fpath, oid = evaluador_ia.guardar_ficha_evaluada(c, ev, hoy=hoy)
            if fpath:
                creadas += 1
                urls_existentes.add(limpiar_url_dedup(c["url"]))
                print(f"   ✓ Guardada en: {fpath.relative_to(ROOT)}\n", flush=True)
            else:
                print(f"   ℹ Ya existía ({oid}).\n", flush=True)
        else:
            print(f"   Link: {c['url']}\n", flush=True)

    if usar_ia:
        guardar_seen_cache(seen_cache)
        print(f"Resumen final: {evaluadas} nuevas evaluadas en profundidad · {creadas} guardadas en ofertas/{hoy}/", flush=True)
    else:
        print(f"Resumen final: {evaluadas} mejores candidatas mostradas (0 tokens). Usa --crear-ofertas para evaluar y guardar.", flush=True)


if __name__ == "__main__":
    main()
