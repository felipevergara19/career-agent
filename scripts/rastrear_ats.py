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
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}

KEYWORDS_FINOPS = ["finops", "cloud cost", "cost optimization", "cloud financial", r"\bcur\b", "cost transparency", "cloud spend", "savings plans"]
KEYWORDS_DEVOPS_AWS = ["aws", "cloud engineer", "cloudops", "infrastructure engineer", "devops", "platform engineer", "sre"]
KEYWORDS_SECOPS = ["secops", "cloud security", "siem", "wazuh", r"\biam\b", "devsecops", "guardduty", "security engineer", "cybersecurity"]
KEYWORDS_AI_AUTO = ["ai automation", "n8n", "workflow automation", "llm automation", "automation engineer", "ai engineer"]

ALL_KEYWORDS = KEYWORDS_FINOPS + KEYWORDS_DEVOPS_AWS + KEYWORDS_SECOPS + KEYWORDS_AI_AUTO

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
    r"authorized to work in the u\.?s|us citizens? only|security clearance|"
    r"eu only|europe only|uk only|united kingdom only|canada only|emea only|apac only|india only|"
    r"germany only|spain only|france only|australia only|brazil only|mexico only|colombia only|"
    r"united states \(remote\)|remote - us\b|remote, us\b|remote \(us\)|remote \(usa\)|remote - uk\b|remote - eu\b|remote - canada\b|remote - emea\b)\b",
    re.I,
)
RE_CIUDAD_SIN_REMOTO = re.compile(
    r"\b(san francisco|new york|austin|seattle|boston|chicago|los angeles|london|berlin|paris|amsterdam|dublin|toronto|vancouver|sydney|singapore|tokyo|bangalore|bengaluru|tel aviv|madrid|barcelona|lisbon|munich|zurich|denver|atlanta|miami|dallas|washington,?\s*dc|raleigh|salt lake|menlo park|mountain view|palo alto)\b",
    re.I,
)
RE_CHILE_LATAM_GLOBAL = re.compile(
    r"\b(chile|santiago|latam|latin america|south america|worldwide|anywhere|global|americas|international|remote worldwide|work from anywhere|remoto)\b",
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
    ]:
        m = re.search(pattern, url)
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
                "url": j.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{j.get('id')}",
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception:
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
                "url": j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}",
                "descripcion": desc[:9000],
            })
        return jobs
    except Exception:
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
                "url": j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j.get('id')}",
                "descripcion": f"{desc} {extra_lists}"[:9000],
            })
        return jobs
    except Exception:
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
            jobs.append({
                "empresa": (j.get("company") or {}).get("name") or slug.capitalize(),
                "portal": "ATS Oficial (SmartRecruiters)",
                "ats": "smartrecruiters",
                "id": str(pid),
                "titulo": j.get("name") or "",
                "ubicacion": f"Remoto ({ubic})" if is_rem else ubic,
                "workplace_type": "remote" if is_rem else "",
                "url": f"https://jobs.smartrecruiters.com/{slug}/{pid}",
                "descripcion": f"{j.get('name', '')} {ubic} {(j.get('department') or {}).get('label', '')}",
            })
        return jobs
    except Exception:
        return []


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
    """API pública JSON de Jobicy (remoto tech global y LatAm)."""
    jobs: list[dict] = []
    for url in [
        "https://jobicy.com/api/v2/remote-jobs?count=50&industry=dev",
        "https://jobicy.com/api/v2/remote-jobs?count=30&geo=latam",
    ]:
        try:
            data = _get_json(url, timeout=10)
            for j in data.get("jobs", []):
                sal = "Sueldo por confirmar"
                if j.get("annualSalaryMin") and j.get("annualSalaryMax"):
                    sal = f"${j['annualSalaryMin']} - ${j['annualSalaryMax']} {j.get('salaryCurrency', 'USD')}/yr"
                jobs.append({
                    "empresa": j.get("companyName") or "Jobicy",
                    "portal": "Jobicy",
                    "ats": "jobicy",
                    "id": str(j.get("id")),
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
    """API pública de Get on Board — excelente fuente para Chile y LatAm 100% remoto."""
    queries = ["finops", "aws cloud", "devsecops", "cloud security", "secops", "ai automation"]
    seen: set[str] = set()
    jobs: list[dict] = []
    for q in queries:
        try:
            url = f"https://www.getonbrd.com/api/v0/search/jobs?query={urllib.parse.quote(q)}&per_page=15"
            data = _get_json(url, timeout=10)
            for item in data.get("data", []):
                jid = str(item.get("id") or "")
                if jid in seen:
                    continue
                seen.add(jid)
                attr = item.get("attributes") or {}
                if not attr.get("remote"):
                    continue
                modality = str(attr.get("remote_modality") or "")
                if modality in ("no_remote", "hybrid", "temporarily_remote"):
                    continue
                empresa = (attr.get("company") or {}).get("data", {}).get("attributes", {}).get("name") or "Empresa GetOnBoard"
                zone = attr.get("remote_zone") or "LatAm / Chile"
                min_s, max_s = attr.get("min_salary"), attr.get("max_salary")
                sal = f"${min_s:,} - ${max_s:,} USD/mes" if min_s and max_s else "Sueldo por confirmar"
                desc = evaluador_ia.strip_html(
                    f"{attr.get('description') or ''} {attr.get('functions') or ''} {attr.get('benefits') or ''}"
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
        except Exception:
            continue
    return jobs


def consultar_remotive() -> list[dict]:
    jobs: list[dict] = []
    for cat in ("devops", "software-dev"):
        try:
            data = _get_json(f"https://remotive.com/api/remote-jobs?category={cat}&limit=40", timeout=10)
            for j in data.get("jobs", []):
                jobs.append({
                    "empresa": j.get("company_name") or "Remotive",
                    "portal": "Remotive",
                    "ats": "remotive",
                    "id": str(j.get("id")),
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
    try:
        data = _get_json("https://remoteok.com/api", timeout=10)
        for j in data[1:80]:
            if not isinstance(j, dict) or not j.get("position"):
                continue
            sal = ""
            if j.get("salary_min") and j.get("salary_max"):
                sal = f"${j['salary_min']:,} - ${j['salary_max']:,} USD/yr"
            jobs.append({
                "empresa": j.get("company") or "RemoteOK",
                "portal": "RemoteOK",
                "ats": "remoteok",
                "id": str(j.get("id")),
                "titulo": j.get("position") or "",
                "ubicacion": j.get("location") or "Worldwide",
                "workplace_type": "remote",
                "salario": sal or "Sueldo por confirmar",
                "url": j.get("apply_url") or j.get("url") or "",
                "descripcion": evaluador_ia.strip_html(j.get("description") or "")[:9000],
            })
    except Exception:
        pass
    return jobs


def consultar_himalayas() -> list[dict]:
    jobs: list[dict] = []
    try:
        data = _get_json("https://himalayas.app/jobs/api?limit=50", timeout=10)
        for j in data.get("jobs", []):
            locs = j.get("locationRestrictions") or []
            ubic = ", ".join(locs) if locs else "Worldwide"
            jobs.append({
                "empresa": j.get("companyName") or "Himalayas",
                "portal": "Himalayas",
                "ats": "himalayas",
                "id": str(j.get("guid") or j.get("applicationLink") or ""),
                "titulo": j.get("title") or "",
                "ubicacion": ubic,
                "workplace_type": "remote",
                "salario": "Sueldo por confirmar",
                "url": j.get("applicationLink") or "",
                "descripcion": evaluador_ia.strip_html(j.get("description") or "")[:9000],
            })
    except Exception:
        pass
    return jobs


# -------------------------------------------------------- Pre-filtro estricto
def pasa_filtros_duros(job: dict) -> tuple[bool, str]:
    """Filtra basura evidente ANTES de llamar a la IA o crear archivos."""
    titulo = job.get("titulo") or ""
    ubic = job.get("ubicacion") or ""
    wp = (job.get("workplace_type") or "").lower()
    desc_head = (job.get("descripcion") or "")[:2500]

    # 1. Descartar Managers / Directors / Staff / Principal / Non-tech
    if RE_EXCLUIR_TITULO.search(titulo):
        return False, "rol de gestión o seniority Staff/Principal/Director"

    # 2. Descartar presencial o híbrido
    if wp in ("onsite", "on-site", "hybrid") or RE_HIBRIDO_PRESENCIAL.search(ubic):
        return False, f"no es 100% remoto ({ubic or wp})"

    # 3. Descartar restricciones explícitas de otros países (US only, EU only, UK only, etc.)
    if RE_SOLO_OTRO_PAIS.search(ubic) or RE_SOLO_OTRO_PAIS.search(desc_head):
        if not re.search(r"\b(chile|latam|latin america|worldwide|anywhere)\b", ubic, re.I):
            return False, f"restringido fuera de Chile/LatAm ({ubic})"

    # 4. Si la ubicación nombra una ciudad específica de US/EU/APAC sin decir Remote/Global/LatAm/Chile
    if RE_CIUDAD_SIN_REMOTO.search(ubic) and not RE_CHILE_LATAM_GLOBAL.search(ubic) and "remote" not in ubic.lower():
        return False, f"ubicación fija fuera de Chile ({ubic})"

    # 5. Si no viene de un portal 100% remoto, exigir alguna señal de remoto o LatAm/Chile/Global
    if wp != "remote" and not RE_CHILE_LATAM_GLOBAL.search(ubic) and "remote" not in ubic.lower():
        if not re.search(r"\b(100%\s*remote|fully remote|work from anywhere|remote\s*[-–]?\s*(latam|global|worldwide|chile))\b", desc_head, re.I):
            return False, f"sin señal de 100% remoto ({ubic or 'sin ubicación'})"

    return True, "ok"


def filtrar_trabajos(jobs: list[dict], keywords_filtro: list[str] | None = None) -> tuple[list[dict], dict]:
    if not keywords_filtro:
        keywords_filtro = ALL_KEYWORDS

    stats = {"descartadas_ubicacion_o_rol": 0, "sin_keywords": 0}
    coincidencias = []
    seen_urls: set[str] = set()

    for j in jobs:
        url = j.get("url") or ""
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        texto_kw = f"{j.get('titulo', '')} {(j.get('descripcion') or '')[:1200]}".lower()
        matched_kw = []
        for kw in keywords_filtro:
            pattern = kw if kw.startswith(r"\b") else r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, texto_kw, re.IGNORECASE):
                matched_kw.append(kw.replace(r"\b", ""))
        if not matched_kw:
            stats["sin_keywords"] += 1
            continue

        ok, motivo = pasa_filtros_duros(j)
        if not ok:
            stats["descartadas_ubicacion_o_rol"] += 1
            continue

        j["keywords_matched"] = matched_kw[:10]
        coincidencias.append(j)

    return coincidencias, stats


def ya_existe(url: str) -> bool:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "estado.py"), "existe", url],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return res.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buscar", action="store_true", help="Buscar vacantes en ATS + portales remotos.")
    parser.add_argument(
        "--foco",
        type=str,
        choices=["finops", "devsecops", "secops", "secops-siem", "ai", "ai-automation", "todos"],
        default="todos",
    )
    parser.add_argument("--agregar", nargs=2, metavar=("ATS", "SLUG"))
    parser.add_argument("--descubrir-url", type=str)
    parser.add_argument("--empresa", type=str)
    parser.add_argument("--solo-ats", action="store_true", help="Omitir portales remotos públicos.")
    parser.add_argument("--crear-ofertas", action="store_true", help="Evaluar y crear fichas en ofertas/<hoy>/.")
    parser.add_argument("--guardar-descartadas", action="store_true", help="Guardar también ofertas que la IA descarte.")
    parser.add_argument("--max-eval", type=int, default=15, help="Tope de ofertas nuevas a evaluar por corrida.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.agregar:
        agregar_empresa(args.agregar[0], args.agregar[1])
        return
    if args.descubrir_url:
        descubrir_desde_url(args.descubrir_url)
        return

    catalogo = cargar_catalogo()
    kws = {
        "finops": KEYWORDS_FINOPS,
        "secops": KEYWORDS_SECOPS,
        "secops-siem": KEYWORDS_SECOPS,
        "ai": KEYWORDS_AI_AUTO,
        "ai-automation": KEYWORDS_AI_AUTO,
        "devsecops": KEYWORDS_DEVOPS_AWS,
    }.get(args.foco, ALL_KEYWORDS)

    todos_los_trabajos: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        tasks = []
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
        if not args.solo_ats and not args.empresa:
            tasks.append(ex.submit(consultar_getonboard))
            tasks.append(ex.submit(consultar_wwr))
            tasks.append(ex.submit(consultar_jobicy))
            tasks.append(ex.submit(consultar_remotive))
            tasks.append(ex.submit(consultar_remoteok))
            tasks.append(ex.submit(consultar_himalayas))

        for fut in concurrent.futures.as_completed(tasks):
            try:
                res = fut.result()
                if res:
                    todos_los_trabajos.extend(res)
            except Exception:
                pass

    coincidencias, stats = filtrar_trabajos(todos_los_trabajos, kws)
    prov = evaluador_ia.detectar_proveedor_ia()

    if args.json:
        print(json.dumps(coincidencias, indent=2, ensure_ascii=False))
        return

    print(f"\n=======================================================")
    print(f"🔎 RASTREO MULTI-FUENTE ({len(todos_los_trabajos)} vacantes descargadas)")
    print(f"🚫 Filtradas por ubicación/modalidad/rol: {stats['descartadas_ubicacion_o_rol']}")
    print(f"🎯 Candidatas pre-filtradas (100% remoto elegible): {len(coincidencias)}")
    print(f"🤖 Evaluador activo: {prov['provider']}/{prov['model']} + Neo4j GraphRAG")
    print(f"=======================================================\n", flush=True)

    hoy = datetime.date.today().strftime("%Y-%m-%d")
    evaluadas = 0
    creadas = 0

    for c in coincidencias:
        if args.max_eval and evaluadas >= args.max_eval:
            print(f"ℹ Tope --max-eval ({args.max_eval}) alcanzado en esta corrida.")
            break
        if ya_existe(c["url"]):
            continue

        evaluadas += 1
        ev = evaluador_ia.evaluar_oferta(c)
        icono = {"alta": "🔥", "media": "✅", "stretch": "⚠️", "descartada": "❌"}.get(ev.get("prioridad"), "•")
        print(f"{icono} [{ev.get('match_score', 0)}/100 · {ev.get('prioridad')}] {c['empresa']} — {c['titulo']}")
        print(f"   Portal: {c['portal']} | Modalidad: {ev.get('modalidad_real')} | Salario: {ev.get('salario_detectado')}")
        print(f"   Resumen: {ev.get('resumen_ia')}")
        if ev.get("logros_grafo_match"):
            print(f"   Grafo Match: {', '.join(ev['logros_grafo_match'][:2])}")

        if args.crear_ofertas:
            if ev.get("prioridad") == "descartada" and not args.guardar_descartadas:
                print(f"   ↳ Omitida (descartada: {ev.get('motivo_descarte')})\n", flush=True)
                continue
            fpath, oid = evaluador_ia.guardar_ficha_evaluada(c, ev, hoy=hoy)
            if fpath:
                creadas += 1
                print(f"   ✓ Guardada en: {fpath.relative_to(ROOT)}\n", flush=True)
            else:
                print(f"   ℹ Ya existía ({oid}).\n", flush=True)
        else:
            print(f"   Link: {c['url']}\n", flush=True)

    print(f"Resumen final: {evaluadas} nuevas evaluadas · {creadas} guardadas en ofertas/{hoy}/", flush=True)


if __name__ == "__main__":
    main()
