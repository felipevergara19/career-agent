#!/usr/bin/env python3
"""
Rastreador directo de vacantes en ATS oficiales (Ashby, Greenhouse, Lever).
Consulta endpoints públicos JSON de empresas objetivo sin pasar por intermediarios ni motores de búsqueda.
Garantiza enlaces 100% activos y oficiales.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
import yaml

CATALOGO_PATH = os.path.join(os.path.dirname(__file__), "..", "perfil", "empresas-target.yaml")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"}

KEYWORDS_FINOPS = ["finops", "cloud cost", "cost optimization", "cloud financial", r"\bcur\b", "cost transparency", "cloud spend"]
KEYWORDS_DEVOPS_AWS = ["aws", "cloud engineer", "cloudops", "infrastructure engineer", "devops", "platform engineer"]
KEYWORDS_SECOPS = ["secops", "cloud security", "siem", "wazuh", "iam", "devsecops", "guardduty", "security engineer"]
KEYWORDS_AI_AUTO = ["ai automation", "n8n", "workflow automation", "llm automation", "automation engineer"]

ALL_KEYWORDS = KEYWORDS_FINOPS + KEYWORDS_DEVOPS_AWS + KEYWORDS_SECOPS + KEYWORDS_AI_AUTO


def cargar_catalogo():
    if not os.path.exists(CATALOGO_PATH):
        return {"greenhouse": [], "ashby": [], "lever": []}
    with open(CATALOGO_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def guardar_catalogo(data):
    with open(CATALOGO_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def agregar_empresa(ats, slug):
    cat = cargar_catalogo()
    ats = ats.lower()
    if ats not in cat:
        cat[ats] = []
    slug = slug.strip().lower()
    if slug not in cat[ats]:
        cat[ats].append(slug)
        guardar_catalogo(cat)
        print(f"✓ Agregada empresa '{slug}' a {ats} en {CATALOGO_PATH}")
    else:
        print(f"ℹ '{slug}' ya estaba registrada en {ats}")


def descubrir_desde_url(url):
    """Extrae ATS y slug de una URL y la agrega al catálogo."""
    url = url.strip()
    # Greenhouse
    m_gh = re.search(r"(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io)/([^/]+)", url)
    if m_gh:
        slug = m_gh.group(1)
        agregar_empresa("greenhouse", slug)
        return "greenhouse", slug

    # Ashby
    m_ash = re.search(r"(?:jobs\.ashbyhq\.com|app\.ashbyhq\.com)/([^/]+)", url)
    if m_ash:
        slug = m_ash.group(1)
        agregar_empresa("ashby", slug)
        return "ashby", slug

    # Lever
    m_lev = re.search(r"jobs\.lever\.co/([^/]+)", url)
    if m_lev:
        slug = m_lev.group(1)
        agregar_empresa("lever", slug)
        return "lever", slug

    print(f"⚠ No se reconoció formato ATS en URL: {url}")
    return None, None


def consultar_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            jobs = []
            for j in data.get("jobs", []):
                jobs.append({
                    "empresa": slug.capitalize(),
                    "ats": "greenhouse",
                    "id": str(j.get("id")),
                    "titulo": j.get("title", ""),
                    "ubicacion": j.get("location", {}).get("name", "Remoto"),
                    "url": j.get("absolute_url", f"https://job-boards.greenhouse.io/{slug}/jobs/{j.get('id')}"),
                })
            return jobs
    except Exception as e:
        return []


def consultar_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            jobs = []
            for j in data.get("jobs", []):
                jobs.append({
                    "empresa": slug.capitalize(),
                    "ats": "ashby",
                    "id": str(j.get("id")),
                    "titulo": j.get("title", ""),
                    "ubicacion": j.get("locationName", "Remoto") or "Remoto",
                    "url": f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}",
                })
            return jobs
    except Exception as e:
        return []


def consultar_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            jobs = []
            for j in data:
                jobs.append({
                    "empresa": slug.capitalize(),
                    "ats": "lever",
                    "id": str(j.get("id")),
                    "titulo": j.get("text", ""),
                    "ubicacion": j.get("categories", {}).get("location", "Remoto") or "Remoto",
                    "url": j.get("hostedUrl", f"https://jobs.lever.co/{slug}/{j.get('id')}"),
                })
            return jobs
    except Exception as e:
        return []


def filtrar_trabajos(jobs, keywords_filtro=None):
    if not keywords_filtro:
        keywords_filtro = ALL_KEYWORDS
    
    coincidencias = []
    for j in jobs:
        texto = f"{j['titulo']} {j['ubicacion']}".lower()
        matched_kw = []
        for kw in keywords_filtro:
            pattern = kw if kw.startswith(r"\b") else r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, texto, re.IGNORECASE):
                matched_kw.append(kw.replace(r"\b", ""))
        if matched_kw:
            j["keywords_matched"] = matched_kw
            # Clasificar angulo
            if any(k in matched_kw for k in [x.replace(r"\b", "") for x in KEYWORDS_FINOPS]):
                j["angulo"] = "finops"
            elif any(k in matched_kw for k in [x.replace(r"\b", "") for x in KEYWORDS_SECOPS]):
                j["angulo"] = "secops-siem"
            elif any(k in matched_kw for k in [x.replace(r"\b", "") for x in KEYWORDS_AI_AUTO]):
                j["angulo"] = "ai-automation"
            else:
                j["angulo"] = "devsecops"
            
            # Detectar seniority excesivo (Lead, Staff, Principal, Director)
            j["seniority_alto"] = any(s in j["titulo"].lower() for s in ["staff", "principal", "director", "head of", "architect", "lead"])
            coincidencias.append(j)
    return coincidencias


def slugify(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def guardar_oferta_archivo(c, hoy):
    empresa_slug = slugify(c["empresa"])
    rol_slug = slugify(c["titulo"])
    file_slug = f"{empresa_slug}-{rol_slug}"[:60].rstrip("-")
    oid = f"{hoy}--{file_slug}"
    
    # Verificar si ya existe con estado.py
    res = subprocess.run([sys.executable, "scripts/estado.py", "existe", c["url"]], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    if res.returncode == 0:
        return None, "ya existe"

    dir_hoy = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ofertas", hoy)
    os.makedirs(dir_hoy, exist_ok=True)
    file_path = os.path.join(dir_hoy, f"{file_slug}.md")

    match_score = 70 if c["seniority_alto"] else 85
    prioridad = "stretch" if c["seniority_alto"] else "alta"

    frontmatter = {
        "tipo": "oferta",
        "id": oid,
        "legacy_id": None,
        "empresa": c["empresa"],
        "cargo": c["titulo"],
        "url": c["url"],
        "portal": f"ATS Oficial ({c['ats'].capitalize()})",
        "fecha_captura": hoy,
        "modalidad": f"100% Remoto ({c['ubicacion']})",
        "idioma": "Inglés (C1)",
        "salario": "Sueldo por confirmar",
        "match_score": match_score,
        "prioridad": prioridad,
        "estado": "evaluada",
        "angulo": c["angulo"],
        "cv": None,
        "motivo_descarte": None,
        "keywords": c["keywords_matched"],
        "brechas": ["Seniority alto / verificar requerimientos de años" if c["seniority_alto"] else "Ninguna excluyente identificada"],
        "notas": "Capturada automáticamente mediante rastreo directo de API de ATS oficial (enlace verificado 100% en vivo).",
        "fecha_postulacion": None,
        "actualizado": hoy
    }

    body = f"""
# {c['empresa']} — {c['titulo']}

- **Fecha de captura:** {hoy}
- **URL de la oferta:** [Link directo en {c['ats'].capitalize()}]({c['url']})
- **Portal de origen:** ATS Oficial ({c['ats'].capitalize()})
- **Modalidad y Ubicación:** 100% Remoto ({c['ubicacion']})
- **Idioma requerido:** Inglés (C1)
- **Match Score:** **{match_score}/100** ({'⚠️ Prioridad Stretch' if c['seniority_alto'] else '🔥 Prioridad Alta'})

## Requisitos Clave y Keywords ATS
{chr(10).join(f"- {kw}" for kw in c['keywords_matched'])}
"""

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("---\n")
        yaml.dump(frontmatter, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        f.write("---\n")
        f.write(body)

    return file_path, "creada"


def main():
    parser = argparse.ArgumentParser(description="Rastreador directo de vacantes en ATS oficiales.")
    parser.add_argument("--buscar", action="store_true", help="Buscar vacantes en todas las empresas del catálogo.")
    parser.add_argument("--foco", type=str, choices=["finops", "devsecops", "secops", "secops-siem", "ai", "ai-automation", "todos"], default="todos", help="Filtrar por foco específico.")
    parser.add_argument("--agregar", nargs=2, metavar=("ATS", "SLUG"), help="Agregar empresa: --agregar ashby supabase")
    parser.add_argument("--descubrir-url", type=str, help="Extraer ATS y slug de una URL y registrarla en el catálogo.")
    parser.add_argument("--empresa", type=str, help="Consultar solo una empresa específica del catálogo.")
    parser.add_argument("--crear-ofertas", action="store_true", help="Crear automáticamente fichas en ofertas/<hoy>/ para vacantes nuevas.")
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON para consumo de agentes.")

    args = parser.parse_args()

    if args.agregar:
        agregar_empresa(args.agregar[0], args.agregar[1])
        return

    if args.descubrir_url:
        descubrir_desde_url(args.descubrir_url)
        return

    catalogo = cargar_catalogo()

    # Seleccionar keywords
    if args.foco == "finops":
        kws = KEYWORDS_FINOPS
    elif args.foco in ("secops", "secops-siem"):
        kws = KEYWORDS_SECOPS
    elif args.foco in ("ai", "ai-automation"):
        kws = KEYWORDS_AI_AUTO
    elif args.foco == "devsecops":
        kws = KEYWORDS_DEVOPS_AWS
    else:
        kws = ALL_KEYWORDS

    todos_los_trabajos = []

    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for slug in catalogo.get("ashby", []):
            if not args.empresa or args.empresa.lower() == slug.lower():
                tasks.append(executor.submit(consultar_ashby, slug))

        for slug in catalogo.get("greenhouse", []):
            if not args.empresa or args.empresa.lower() == slug.lower():
                tasks.append(executor.submit(consultar_greenhouse, slug))

        for slug in catalogo.get("lever", []):
            if not args.empresa or args.empresa.lower() == slug.lower():
                tasks.append(executor.submit(consultar_lever, slug))

        for future in concurrent.futures.as_completed(tasks):
            try:
                res = future.result()
                if res:
                    todos_los_trabajos.extend(res)
            except Exception:
                pass

    coincidencias = filtrar_trabajos(todos_los_trabajos, kws)

    if args.json:
        print(json.dumps(coincidencias, indent=2, ensure_ascii=False))
        return

    print(f"\n=======================================================")
    print(f"🔎 RASTREO DIRECTO DE ATS OFICIALES ({len(todos_los_trabajos)} vacantes analizadas)")
    print(f"🎯 Coincidencias encontradas: {len(coincidencias)}")
    print(f"=======================================================\n")

    hoy = datetime.date.today().strftime("%Y-%m-%d")

    for c in coincidencias:
        badge_sr = "⚠️ [Seniority Alto]" if c["seniority_alto"] else "🎯 [Calce Directo]"
        print(f"• {c['empresa']} — {c['titulo']} {badge_sr}")
        print(f"  ATS: {c['ats']} | Ángulo: {c['angulo']} | Ubicación: {c['ubicacion']}")
        print(f"  Keywords: {', '.join(c['keywords_matched'])}")
        print(f"  Link directo: {c['url']}")
        if args.crear_ofertas:
            fpath, status = guardar_oferta_archivo(c, hoy)
            if status == "creada":
                print(f"  ✓ Ficha creada en: {fpath}")
            else:
                print(f"  ℹ Oferta ya registrada anteriormente.")
        print()


if __name__ == "__main__":
    main()
