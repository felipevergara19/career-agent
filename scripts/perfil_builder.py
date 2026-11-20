#!/usr/bin/env python3
"""Motor de Onboarding, Ingesta de Documentos y Perfil Estructurado para Career Agent.

Permite a cualquier usuario:
  1. Subir documentos propios (PDF de LinkedIn, CV existente en PDF/DOCX/MD/TXT,
     certificados, portafolio) y extraer su texto localmente.
  2. Importar URLs públicas (LinkedIn, GitHub, portafolio personal).
  3. Contar su historia profesional en texto libre ("cuéntanos tu vida y trayectoria").
  4. Definir puestos buscados, áreas/ángulos con keywords, condiciones no negociables,
     rango salarial, idiomas y rúbrica 0-100.
  5. Sintetizar todo con IA (regla de veracidad estricta: cero invención) o guardar
     manualmente, generando en sincronía:
       - perfil/perfil-estructurado.json  (fuente estructurada para GraphRAG, CV y Rastreo)
       - perfil/perfil-maestro.md         (compatible con todos los agentes/skills)
       - perfil/preferencias.md           (filtros duros, metas salariales y rúbrica)
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
import re
import shutil
import sys
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERFIL_DIR = ROOT / "perfil"
DOCS_DIR = PERFIL_DIR / "documentos"
DOCS_INDEX_PATH = DOCS_DIR / "index.json"
STRUCTURED_PATH = PERFIL_DIR / "perfil-estructurado.json"
PERFIL_MD_PATH = PERFIL_DIR / "perfil-maestro.md"
PREF_MD_PATH = PERFIL_DIR / "preferencias.md"
CV_BASE_DIR = ROOT / "cvs-generados" / "base"

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".txt", ".json", ".csv", ".html", ".htm"}

DEFAULT_PERFIL: dict = {
    "version": 1,
    "actualizado": "",
    "datos_personales": {
        "nombre": "",
        "email": "",
        "telefono": "",
        "ubicacion": "",
        "pais": "",
        "zona_horaria": "",
        "linkedin": "",
        "github": "",
        "portafolio": "",
        "idiomas": [
            {"idioma": "Español", "nivel": "Nativo"},
            {"idioma": "Inglés", "nivel": "B2"},
        ],
    },
    "titular_principal": "",
    "resumen_profesional": "",
    "historia_libre": "",
    "urls_referencia": [],
    "cv_base": {
        "modo": "",
        "ruta": "",
        "doc_id": None,
        "nombre_archivo": "",
    },
    "skills": {
        "avanzado": [],
        "intermedio": [],
        "aprendiendo": [],
    },
    "experiencias": [],
    "impact_log": [],
    "educacion": [],
    "certificaciones": [],
    "preferencias": {
        "pais_residencia": "",
        "regiones_aceptadas": ["LatAm", "Global", "Worldwide", "Remote"],
        "modalidad": "remoto_100",
        "seniority_objetivo": "mid",
        "anos_experiencia": 3,
        "solo_ic": True,
        "puestos_objetivo": [],
        "areas": [],
        "salario": {
            "moneda": "USD",
            "periodo": "mes",
            "minimo": 0,
            "ideal": 0,
        },
        "idiomas_trabajo": ["Español", "Inglés"],
        "no_negociables": ["100% Remoto"],
        "exclusiones": ["presencial", "híbrido", "us only", "manager", "director"],
        "rubrica": {
            "tecnico": 35,
            "modalidad": 20,
            "idioma": 15,
            "salario": 15,
            "seniority": 15,
        },
    },
    "preguntas_faltantes": [],
    "sintetizado_por": "",
}

MODALIDAD_LABELS = {
    "remoto_100": "100% Remoto",
    "hibrido": "Híbrido",
    "presencial": "Presencial",
    "cualquiera": "Remoto / Híbrido / Presencial",
}

SENIORITY_LABELS = {
    "junior": "Junior (0–2 años)",
    "mid": "Mid-Level / Semi-Senior (2–4 años)",
    "senior": "Senior (5+ años)",
    "lead": "Staff / Lead / Principal",
    "manager": "Manager / Director / Head",
}


def slugify(text: str, fallback: str = "general") -> str:
    s = (text or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"), ("ü", "u")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or fallback


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = copy.deepcopy(v)
    return out


# ------------------------------------------------------------ Document Vault
def _cargar_indice_docs() -> list[dict]:
    if not DOCS_INDEX_PATH.is_file():
        return []
    try:
        data = json.loads(DOCS_INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _guardar_indice_docs(docs: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_INDEX_PATH.write_text(json.dumps(docs, indent=2, ensure_ascii=False), encoding="utf-8")


def listar_documentos() -> list[dict]:
    docs = _cargar_indice_docs()
    out = []
    for d in docs:
        txt_path = ROOT / d.get("ruta_texto", "") if d.get("ruta_texto") else None
        preview = ""
        if txt_path and txt_path.is_file():
            preview = txt_path.read_text(encoding="utf-8", errors="ignore")[:320]
        item = dict(d)
        item["preview"] = preview
        out.append(item)
    return out


def extraer_texto_bytes(content: bytes, filename: str) -> str:
    """Extrae texto limpio desde PDF, DOCX, Markdown, TXT, JSON, CSV o HTML."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        # 1. Intentar con pypdf (instalado en Docker / requirements.txt)
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            pages = []
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    pages.append(t.strip())
            if pages:
                return "\n\n".join(pages)
        except Exception:
            pass
        # 2. Fallback ligero: intentar extraer strings legibles del stream PDF
        raw_str = content.decode("latin-1", errors="ignore")
        chunks = re.findall(r"\(([^()]{3,200})\)", raw_str)
        cleaned = " ".join(c for c in chunks if sum(ch.isalnum() for ch in c) >= 3)
        return re.sub(r"\s+", " ", cleaned).strip()

    if ext == ".docx":
        # 1. Intentar con python-docx
        try:
            import docx

            doc = docx.Document(io.BytesIO(content))
            lines = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                    if cells:
                        lines.append(" | ".join(cells))
            if lines:
                return "\n".join(lines)
        except Exception:
            pass
        # 2. Fallback stdlib: leer word/document.xml del zip DOCX
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            texts = [el.text for el in root.iter() if el.tag.endswith("}t") and el.text]
            return re.sub(r"\s+", " ", " ".join(texts)).strip()
        except Exception:
            return ""

    if ext in (".html", ".htm"):
        raw = content.decode("utf-8", errors="ignore")
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"<[^>]+>", " ", raw)
        return re.sub(r"\s+", " ", raw).strip()

    return content.decode("utf-8", errors="ignore").strip()


def guardar_documento(
    content: bytes,
    filename: str,
    categoria: str = "cv_actual",
    usar_como_cv_base: bool = False,
) -> dict:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("El archivo supera el límite de 15 MB.")
    orig_name = Path(filename or "documento.txt").name
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Formato '{ext}' no soportado. Usa PDF, DOCX, MD, TXT, JSON o HTML.")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    doc_id = f"doc-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    safe_stem = slugify(Path(orig_name).stem, fallback="documento")
    stored_name = f"{doc_id}-{safe_stem}{ext}"
    stored_path = DOCS_DIR / stored_name
    stored_path.write_bytes(content)

    extracted = extraer_texto_bytes(content, orig_name)
    txt_path = DOCS_DIR / f"{doc_id}.txt"
    txt_path.write_text(extracted, encoding="utf-8")

    # Heurística simple si la categoría vino en auto
    cat = (categoria or "otro").strip().lower()
    if cat == "auto":
        low_name = orig_name.lower()
        low_txt = extracted[:1500].lower()
        if "linkedin" in low_name or "linkedin.com/in/" in low_txt or "extracto" in low_txt and "aptitudes principales" in low_txt:
            cat = "linkedin_pdf"
        elif any(k in low_name for k in ("cv", "resume", "curriculum", "hoja")):
            cat = "cv_actual"
        elif any(k in low_name for k in ("cert", "diploma", "titulo", "badge")):
            cat = "certificado"
        else:
            cat = "otro"

    entry = {
        "id": doc_id,
        "nombre_original": orig_name,
        "categoria": cat,
        "extension": ext,
        "bytes": len(content),
        "caracteres_extraidos": len(extracted),
        "ruta_archivo": str(stored_path.relative_to(ROOT).as_posix()),
        "ruta_texto": str(txt_path.relative_to(ROOT).as_posix()),
        "subido_el": dt.datetime.now().isoformat(timespec="seconds"),
    }

    docs = _cargar_indice_docs()
    docs.insert(0, entry)
    _guardar_indice_docs(docs)

    if usar_como_cv_base or (cat == "cv_actual" and not cargar_perfil_estructurado().get("cv_base", {}).get("ruta")):
        activar_documento_como_cv_base(doc_id)

    entry["preview"] = extracted[:320]
    return entry


def activar_documento_como_cv_base(doc_id: str) -> dict:
    docs = _cargar_indice_docs()
    doc = next((d for d in docs if d["id"] == doc_id), None)
    if not doc:
        raise FileNotFoundError(f"Documento '{doc_id}' no encontrado.")

    src = ROOT / doc["ruta_archivo"]
    if not src.is_file():
        raise FileNotFoundError("El archivo original ya no existe en disco.")

    CV_BASE_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(doc["nombre_original"]).suffix.lower()
    dest = CV_BASE_DIR / f"cv-base-subido{ext}"
    shutil.copy2(src, dest)

    perfil = cargar_perfil_estructurado()
    perfil["cv_base"] = {
        "modo": "subido",
        "ruta": str(dest.relative_to(ROOT).as_posix()),
        "doc_id": doc_id,
        "nombre_archivo": doc["nombre_original"],
    }
    guardar_perfil_estructurado(perfil, regenerar_md=False)
    return perfil["cv_base"]


def eliminar_documento(doc_id: str) -> bool:
    docs = _cargar_indice_docs()
    target = next((d for d in docs if d["id"] == doc_id), None)
    if not target:
        return False

    for key in ("ruta_archivo", "ruta_texto"):
        rel = target.get(key)
        if rel:
            (ROOT / rel).unlink(missing_ok=True)

    docs = [d for d in docs if d["id"] != doc_id]
    _guardar_indice_docs(docs)

    perfil = cargar_perfil_estructurado()
    if perfil.get("cv_base", {}).get("doc_id") == doc_id:
        perfil["cv_base"] = {"modo": "", "ruta": "", "doc_id": None, "nombre_archivo": ""}
        guardar_perfil_estructurado(perfil, regenerar_md=False)
    return True


def importar_url_perfil(url: str, tipo: str = "auto") -> dict:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Ingresa una URL válida que empiece con http:// o https://")

    low = url.lower()
    if tipo == "auto":
        if "linkedin.com" in low:
            tipo = "linkedin"
        elif "github.com" in low:
            tipo = "github"
        else:
            tipo = "portfolio"

    perfil = cargar_perfil_estructurado()
    if tipo == "linkedin":
        perfil["datos_personales"]["linkedin"] = url
    elif tipo == "github":
        perfil["datos_personales"]["github"] = url
    elif tipo == "portfolio":
        perfil["datos_personales"]["portafolio"] = url

    aviso = ""
    texto = ""
    titulo = url
    try:
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
        # Si es GitHub perfil público, aprovechar su API JSON pública sin auth
        m_gh = re.match(r"https?://(?:www\.)?github\.com/([A-Za-z0-9_-]+)/?$", url)
        if m_gh:
            user = m_gh.group(1)
            req = urllib.request.Request(f"https://api.github.com/users/{user}", headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                gh = json.loads(resp.read().decode("utf-8", errors="ignore"))
            req_r = urllib.request.Request(
                f"https://api.github.com/users/{user}/repos?sort=updated&per_page=10", headers=headers
            )
            with urllib.request.urlopen(req_r, timeout=10) as resp_r:
                repos = json.loads(resp_r.read().decode("utf-8", errors="ignore"))
            titulo = f"GitHub: {gh.get('name') or user}"
            repo_lines = [
                f"- {r.get('name')}: {r.get('description') or ''} (Lenguaje: {r.get('language') or 'N/A'}, ★{r.get('stargazers_count', 0)})"
                for r in (repos if isinstance(repos, list) else [])
                if not r.get("fork")
            ]
            texto = (
                f"GitHub User: {gh.get('name') or user} (@{user})\n"
                f"Bio: {gh.get('bio') or ''}\n"
                f"Location: {gh.get('location') or ''}\n"
                f"Repositorios destacados:\n" + "\n".join(repo_lines)
            )
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            m_t = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
            if m_t:
                titulo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m_t.group(1))).strip()
            texto = extraer_texto_bytes(raw.encode("utf-8"), "page.html")[:8000]
            if tipo == "linkedin" and (
                len(texto) < 350 or "sign in" in texto.lower()[:400] or "authwall" in raw.lower()
            ):
                texto = ""
                aviso = (
                    "URL de LinkedIn guardada. LinkedIn bloquea lectura automática sin sesión: "
                    "para importar tu perfil completo, en LinkedIn haz clic en 'Más' → 'Guardar en PDF' "
                    "y sube ese PDF arriba (toma 10 segundos)."
                )
    except Exception:
        if tipo == "linkedin":
            aviso = (
                "URL de LinkedIn guardada. LinkedIn restringe el acceso directo a bots; "
                "descarga tu perfil con 'Más → Guardar en PDF' y súbelo en la zona de documentos."
            )
        else:
            aviso = "URL guardada en tu perfil, pero la página no permitió descarga automática."

    urls = [u for u in (perfil.get("urls_referencia") or []) if u.get("url") != url]
    entry = {
        "tipo": tipo,
        "url": url,
        "titulo": titulo[:140],
        "caracteres": len(texto),
        "texto_extraido": texto[:8000],
        "agregado_el": dt.datetime.now().isoformat(timespec="seconds"),
    }
    urls.insert(0, entry)
    perfil["urls_referencia"] = urls[:10]
    guardar_perfil_estructurado(perfil, regenerar_md=True)
    return {"entry": entry, "aviso": aviso}


# --------------------------------------------------------- Structured Profile
def _es_plantilla_ejemplo(texto: str) -> bool:
    """Detecta si un Markdown sigue siendo la plantilla de ejemplo sin personalizar."""
    low = (texto or "").lower()
    marcadores = (
        "[tu nombre completo]",
        "tu nombre completo",
        "tu-email@ejemplo.com",
        "plantilla de ejemplo.",
        "$x,xxx",
    )
    return sum(1 for m in marcadores if m in low) >= 2


def detectar_fuentes_historicas() -> list[dict]:
    """Lista los archivos históricos reales que el usuario ya tenía en el repo
    (perfil-maestro.md, preferencias.md y CVs en cvs-generados/base/)."""
    fuentes: list[dict] = []
    if PERFIL_MD_PATH.is_file():
        txt = PERFIL_MD_PATH.read_text(encoding="utf-8", errors="ignore")
        if txt.strip() and not _es_plantilla_ejemplo(txt):
            fuentes.append({
                "tipo": "perfil_md",
                "nombre": "perfil/perfil-maestro.md",
                "ruta": "perfil/perfil-maestro.md",
                "caracteres": len(txt),
                "descripcion": "Perfil Maestro histórico del repositorio",
            })
    if PREF_MD_PATH.is_file():
        txt = PREF_MD_PATH.read_text(encoding="utf-8", errors="ignore")
        if txt.strip() and not _es_plantilla_ejemplo(txt):
            fuentes.append({
                "tipo": "preferencias_md",
                "nombre": "perfil/preferencias.md",
                "ruta": "perfil/preferencias.md",
                "caracteres": len(txt),
                "descripcion": "Preferencias, no negociables y rúbrica histórica",
            })
    if CV_BASE_DIR.is_dir():
        for f in sorted(CV_BASE_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True):
            fuentes.append({
                "tipo": "cv_base_pdf",
                "nombre": f.name,
                "ruta": str(f.relative_to(ROOT).as_posix()),
                "caracteres": f.stat().st_size,
                "descripcion": "CV Base PDF existente en cvs-generados/base/",
            })
    return fuentes


def _inferir_angulo_texto(texto: str) -> str:
    low = (texto or "").lower()
    if any(k in low for k in ("finops", "savings plan", "cost", "ahorro", "elasticache", "nat gateway", "cur", "duckdb")):
        return "finops"
    if any(k in low for k in ("wazuh", "siem", "bitdefender", "guardduty", "incident.io", "secops", "phishing", "waf", "dkim", "oauth")):
        return "secops-siem"
    if any(k in low for k in ("n8n", "gpt-4", "gemini", "rag", "llm", "llamadas", "ai ", "ia ")):
        return "ai-automation"
    if any(k in low for k in ("iam", "devsecops", "terraform", "ansible", "eks", "kubernetes", "aws", "docker")):
        return "devsecops"
    return "base"


def _parsear_preferencias_md(texto: str, pref: dict) -> None:
    """Extrae puestos objetivo, áreas, geografía, modalidad, salario, no negociables,
    exclusiones y rúbrica desde un perfil/preferencias.md histórico."""
    if not texto.strip() or _es_plantilla_ejemplo(texto):
        return
    low = texto.lower()

    # Modalidad
    if "100% remot" in low or "fully remote" in low or "work from anywhere" in low:
        pref["modalidad"] = "remoto_100"
    elif "híbrido" in low or "hibrido" in low or "hybrid" in low:
        if "descartar" not in low.split("híbrido")[0][-120:]:
            pref["modalidad"] = "hibrido"

    # País y regiones
    for pais_cand in ("Chile", "México", "Colombia", "Argentina", "Perú", "España", "Uruguay", "Brasil", "Estados Unidos"):
        if re.search(rf"\b{re.escape(pais_cand)}\b", texto, re.I):
            pref["pais_residencia"] = pais_cand
            break

    regiones = []
    for reg in ("Chile", "LatAm", "Global", "Worldwide", "Americas", "Remote", "Europe", "US", "España", "México", "Colombia"):
        if re.search(rf"\b{re.escape(reg)}\b", texto, re.I):
            # Evitar agregar regiones que solo aparecen junto a "Only"
            if re.search(rf"{re.escape(reg)}\s+only", texto, re.I) and reg in ("US", "Europe", "España"):
                continue
            regiones.append(reg)
    if regiones:
        pref["regiones_aceptadas"] = list(dict.fromkeys(regiones))

    # Seniority y años
    if re.search(r"1\s*[a–\-]\s*3\s*años|mid-?level|semi-?senior", low):
        pref["seniority_objetivo"] = "mid"
        pref["anos_experiencia"] = 3
    elif re.search(r"junior|0\s*[a–\-]\s*2\s*años", low):
        pref["seniority_objetivo"] = "junior"
        pref["anos_experiencia"] = 1
    elif re.search(r"5\+?\s*años.*senior|senior.*5\+?", low) and "descartar" not in low:
        pref["seniority_objetivo"] = "senior"
        pref["anos_experiencia"] = 5

    # Solo IC
    if "contribuidor individual" in low or "(ic)" in low:
        pref["solo_ic"] = True

    # Roles objetivo (todo lo que esté entre backticks en la sección 2)
    m_roles = re.search(r"##\s*2\..*?(?=##\s*3\.|\Z)", texto, flags=re.S)
    if m_roles:
        roles_sec = m_roles.group(0)
        roles_found = [r.strip() for r in re.findall(r"`([^`]{3,80})`", roles_sec) if r.strip()]
        if roles_found:
            pref["puestos_objetivo"] = list(dict.fromkeys(roles_found))[:16]

    # Idiomas de trabajo
    idiomas_t = []
    if re.search(r"\bingl[eé]s\b|\benglish\b", low):
        idiomas_t.append("Inglés")
    if re.search(r"\bespañol\b|\bspanish\b", low):
        idiomas_t.append("Español")
    if idiomas_t:
        pref["idiomas_trabajo"] = idiomas_t

    # Salario USD/mes
    m_usd = re.search(r"\$([0-9]{1,2}[,\.][0-9]{3})\s*[–\-a]+\s*\$([0-9]{1,2}[,\.][0-9]{3})\s*USD(?:/mes)?", texto, re.I)
    if m_usd:
        pref["salario"] = {
            "moneda": "USD",
            "periodo": "mes",
            "minimo": int(re.sub(r"[^0-9]", "", m_usd.group(1))),
            "ideal": int(re.sub(r"[^0-9]", "", m_usd.group(2))),
        }

    # No negociables (ítems numerados en sección 1)
    m_sec1 = re.search(r"##\s*1\..*?(?=##\s*2\.|\Z)", texto, flags=re.S)
    if m_sec1:
        nn = []
        for m_item in re.finditer(r"^\s*\d+\.\s+\*\*([^*:\n]+)\*\*", m_sec1.group(0), flags=re.M):
            lbl = m_item.group(1).strip().rstrip(":")
            if lbl:
                nn.append(lbl)
        if nn:
            pref["no_negociables"] = nn[:8]

    # Exclusiones
    excls = []
    if "híbrida o presencial" in low or "no es 100% remota" in low:
        excls.extend(["presencial", "híbrido"])
    if "us only" in low:
        excls.append("us only")
    if "eu only" in low:
        excls.append("eu only")
    if "engineering manager" in low or "scrum master" in low:
        excls.extend(["manager", "director", "scrum master", "project manager"])
    if "lead, staff, principal" in low or "exceso de seniority" in low:
        excls.extend(["staff", "principal", "lead"])
    if excls:
        pref["exclusiones"] = list(dict.fromkeys(excls))

    # Áreas explícitas (`- **Nombre** (`slug`): kw1, kw2`) o inferidas por roles/keywords
    areas_expl = []
    for m_a in re.finditer(r"^\s*-\s+\*\*([^*]+)\*\*\s*\(`([a-z0-9_-]+)`\):\s*(.+)$", texto, flags=re.M):
        kws = [k.strip() for k in m_a.group(3).split(",") if k.strip()]
        areas_expl.append({"slug": m_a.group(2).strip(), "nombre": m_a.group(1).strip(), "keywords": kws})
    if areas_expl:
        pref["areas"] = areas_expl
    else:
        areas_inf = []
        roles_joined = " ".join(pref.get("puestos_objetivo") or []).lower() + " " + low[:3000]
        if any(k in roles_joined for k in ("finops", "cost optimization", "cloud financial")):
            areas_inf.append({"slug": "finops", "nombre": "Cloud FinOps & AWS Cost", "keywords": ["finops", "cloud cost", "cost optimization", "aws", "savings plans", "cost explorer", "cur"]})
        if any(k in roles_joined for k in ("devsecops", "aws cloud", "cloud infrastructure", "cloudops")):
            areas_inf.append({"slug": "devsecops", "nombre": "DevSecOps & AWS Cloud", "keywords": ["devsecops", "aws", "iam", "terraform", "cloudops", "cloud engineer", "platform engineer"]})
        if any(k in roles_joined for k in ("secops", "siem", "wazuh", "cloud security", "aws security")):
            areas_inf.append({"slug": "secops-siem", "nombre": "SecOps, SIEM & Cloud Security", "keywords": ["secops", "siem", "wazuh", "guardduty", "cloud security", "iam", "incident response"]})
        if any(k in roles_joined for k in ("ai automation", "finops for ai", "n8n", "llm")):
            areas_inf.append({"slug": "ai-automation", "nombre": "AI Automation & Python", "keywords": ["ai automation", "python", "llm", "n8n", "rag", "workflow automation", "boto3"]})
        if areas_inf:
            pref["areas"] = areas_inf

    # Rúbrica desde tabla Markdown
    rub_map = [
        ("tecnico", r"alineaci[oó]n t[eé]cnica[^\n|]*\|\s*\*{0,2}(\d+)\s*pts"),
        ("modalidad", r"modalidad[^\n|]*\|\s*\*{0,2}(\d+)\s*pts"),
        ("idioma", r"(?:idioma|ingl[eé]s)[^\n|]*\|\s*\*{0,2}(\d+)\s*pts"),
        ("salario", r"(?:salario|banda salarial)[^\n|]*\|\s*\*{0,2}(\d+)\s*pts"),
        ("seniority", r"seniority[^\n|]*\|\s*\*{0,2}(\d+)\s*pts"),
    ]
    rub = dict(pref.get("rubrica") or {})
    for key, pat in rub_map:
        m = re.search(pat, texto, re.I)
        if m and m.lastindex:
            rub[key] = int(m.group(1))
    pref["rubrica"] = rub


def _seed_desde_markdown_existente() -> dict:
    """Si el usuario ya tenía perfil/perfil-maestro.md, perfil/preferencias.md,
    grafo_conocimiento.json o CVs en cvs-generados/base/, carga toda su información
    histórica automáticamente para no pedirle empezar desde cero."""
    perfil = copy.deepcopy(DEFAULT_PERFIL)
    dp = perfil["datos_personales"]

    # 1. Leer perfil/perfil-maestro.md si es real (no plantilla de ejemplo)
    if PERFIL_MD_PATH.is_file():
        texto = PERFIL_MD_PATH.read_text(encoding="utf-8", errors="ignore")
        if texto.strip() and not _es_plantilla_ejemplo(texto):
            def _campo(label: str) -> str:
                m = re.search(rf"^\s*-\s*\*\*{label}:\*\*\s*`?([^`\n]+)`?", texto, flags=re.M | re.I)
                return m.group(1).strip() if m else ""

            dp["nombre"] = _campo("Nombre completo")
            ubic_full = _campo("Ubicación base") or _campo("Ubicación")
            dp["ubicacion"] = ubic_full
            m_tz = re.search(r"\b(CLT|EST|CST|PST|CET|UTC[+-]\d+(?:\s*a\s*UTC[+-]\d+)?)\b", ubic_full)
            if m_tz:
                dp["zona_horaria"] = m_tz.group(0)
            for pais_cand in ("Chile", "México", "Colombia", "Argentina", "Perú", "España", "Uruguay", "Brasil"):
                if re.search(rf"\b{re.escape(pais_cand)}\b", ubic_full, re.I):
                    dp["pais"] = pais_cand
                    break
            dp["email"] = _campo("Email")
            dp["telefono"] = _campo("Teléfono")
            dp["linkedin"] = _campo("LinkedIn")
            dp["github"] = _campo("GitHub")
            dp["portafolio"] = _campo("Portafolio")

            # Idiomas
            idiomas_found = []
            for m_id in re.finditer(r"^\s{2,}-\s+\*\*([^:*]+):\*\*\s*(.+)$", texto, flags=re.M):
                idm_name = m_id.group(1).strip()
                if idm_name.lower() in ("español", "inglés", "ingles", "portugués", "francés", "alemán", "english", "spanish"):
                    lvl = re.sub(r"\*\*", "", m_id.group(2)).strip()
                    # Acortar nivel si trae explicación larga entre paréntesis
                    lvl_short = lvl.split("(")[0].strip() or lvl[:40]
                    idiomas_found.append({"idioma": idm_name, "nivel": lvl_short[:50]})
            if idiomas_found:
                dp["idiomas"] = idiomas_found

            # Titular Principal (soporta misma línea o línea siguiente con backticks)
            m_tit = re.search(
                r"\*\*Titular Principal[^*]*:\*\*\s*(?:\n\s*)?`([^`\n]+)`",
                texto,
                flags=re.I,
            )
            if m_tit:
                perfil["titular_principal"] = m_tit.group(1).strip()
            else:
                perfil["titular_principal"] = _campo("Titular Principal")

            # Resumen profesional (si hay párrafo bajo sección 2 o intro en experiencia)
            m_res = re.search(r"\*Administra[^\n*]+\*", texto)
            if m_res:
                perfil["resumen_profesional"] = re.sub(r"\*+", "", m_res.group(0)).strip().rstrip(":")
            elif perfil["titular_principal"]:
                perfil["resumen_profesional"] = f"{perfil['titular_principal']}."

            # Experiencias (bloques ### **Empresa**)
            exps = []
            sec_exp_m = re.search(r"##\s*3\.\s*Experiencia.*?(?=##\s*4\.|\Z)", texto, flags=re.S | re.I)
            if sec_exp_m:
                sec_exp = sec_exp_m.group(0)
                bloques = re.split(r"(?=^###\s+\*\*)", sec_exp, flags=re.M)
                for bl in bloques:
                    m_h = re.match(r"^###\s+\*\*([^*]+)\*\*(.*)", bl.strip())
                    if not m_h:
                        continue
                    titulo_bl = m_h.group(1).strip()
                    meta_bl = re.sub(r"[*()]", "", m_h.group(2)).strip()
                    if "experiencia previa" in titulo_bl.lower():
                        # Parsear sub-roles como experiencias individuales
                        for m_prev in re.finditer(
                            r"^\s*-\s+\*\*([^*]+)\*\*\s*—\s*\*([^*]+)\*\s*(?:\n\s+-\s+(.+))?",
                            bl,
                            flags=re.M,
                        ):
                            cargo_p = m_prev.group(1).strip()
                            meta_p = [x.strip() for x in m_prev.group(2).split("|")]
                            emp_p = meta_p[0] if meta_p else "Experiencia"
                            per_p = meta_p[1] if len(meta_p) > 1 else ""
                            ub_p = meta_p[2] if len(meta_p) > 2 else ""
                            bul_p = [m_prev.group(3).strip()] if m_prev.group(3) else []
                            exps.append({"empresa": emp_p, "cargo": cargo_p, "periodo": per_p, "ubicacion": ub_p, "rubro": "", "bullets": bul_p})
                        continue

                    m_cargo = re.search(r"\*\*(?:Etapa\s*\d+[^*]*|Rol):\*\*\s*\*{0,2}([^*\n(]+)", bl, flags=re.I)
                    cargo = m_cargo.group(1).strip().rstrip("* ") if m_cargo else titulo_bl
                    m_per = re.search(r"\b(20\d{2}\s*[–\-]\s*(?:Presente|Present|20\d{2})|(?:Ene|Feb|Mar|Abr|May|Jun|Jul|Ago|Sep|Oct|Nov|Dic|Jan|Apr|Aug|Dec)[a-z]*\s+20\d{2}[^|)*\n]*)", bl, flags=re.I)
                    periodo = m_per.group(1).strip() if m_per else ""
                    bullets = []
                    for m_b in re.finditer(r"^\s*-\s+\*\*([^*]{8,140})\*\*:?\s*(.*)$", bl, flags=re.M):
                        b_tit = m_b.group(1).strip().rstrip(":")
                        if b_tit.lower().startswith(("etapa ", "rol", "versión en", "titular ", "no incluir")):
                            continue
                        b_rest = m_b.group(2).strip()
                        bullets.append(f"{b_tit}: {b_rest}" if b_rest else b_tit)
                    exps.append({
                        "empresa": titulo_bl,
                        "cargo": cargo,
                        "periodo": periodo,
                        "ubicacion": "100% Remoto" if "remoto" in bl.lower() else "",
                        "rubro": meta_bl,
                        "bullets": bullets[:10],
                    })
            if exps:
                perfil["experiencias"] = exps

            # Educación y Certificaciones
            m_edu = re.search(r"###\s*Educaci[oó]n(.*?)(?=###|\Z)", texto, flags=re.S | re.I)
            if m_edu:
                for m_e in re.finditer(r"^\s*-\s+\*\*([^*]+)\*\*\s*—\s*\*?([^*\n(]+)\*?\s*(?:\(([^)]+)\))?", m_edu.group(1), flags=re.M):
                    perfil["educacion"].append({
                        "titulo": m_e.group(1).strip(),
                        "institucion": m_e.group(2).strip(),
                        "periodo": (m_e.group(3) or "").strip(),
                    })
            m_cert = re.search(r"###\s*Certificaciones[^\n]*(.*?)(?=##|\Z)", texto, flags=re.S | re.I)
            if m_cert:
                for m_c in re.finditer(r"^\s*-\s+\*\*([^*]+)\*\*\s*—\s*\*?([^*\n(]+)\*?\s*(?:\(([^)]+)\))?", m_cert.group(1), flags=re.M):
                    perfil["certificaciones"].append({
                        "nombre": m_c.group(1).strip(),
                        "entidad": m_c.group(2).strip(),
                        "fecha": re.sub(r"\*+", "", (m_c.group(3) or "")).strip(),
                    })

            # Skills: primero intentar líneas explícitas 🟢/🟡/🔴, luego detectar stack real presente en el perfil
            for m_sk in re.finditer(r"🟢\s*\*\*[^*]+:\*\*\s*(.+)", texto):
                perfil["skills"]["avanzado"].extend([s.strip() for s in m_sk.group(1).split(",") if s.strip()])
            for m_sk in re.finditer(r"🟡\s*\*\*[^*]+:\*\*\s*(.+)", texto):
                perfil["skills"]["intermedio"].extend([s.strip() for s in m_sk.group(1).split(",") if s.strip()])
            if not perfil["skills"]["avanzado"]:
                catalogo_tech = [
                    "AWS", "FinOps", "AWS Cost Explorer", "Savings Plans", "VPC Endpoints", "IAM",
                    "AWS Organizations", "IAM Identity Center", "ECS Fargate", "CloudWatch", "CloudTrail",
                    "GuardDuty", "EventBridge", "SNS", "KMS", "Secrets Manager", "ElastiCache", "RDS", "S3",
                    "SES", "Athena", "EKS", "Terraform", "Ansible", "Python", "Boto3", "Bash", "SQL",
                    "Docker", "Linux", "Wazuh", "SIEM", "Bitdefender", "Cloudflare WAF", "incident.io",
                    "n8n", "LLMs", "PostgreSQL", "Neo4j", "FastAPI", "Git", "CI/CD",
                ]
                perfil["skills"]["avanzado"] = [
                    t for t in catalogo_tech
                    if re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])", texto.lower())
                ][:24]

            # Impact Log limpio extraído directamente de los logros reales en experiencias
            logros_loaded = []
            for idx, exp in enumerate(perfil["experiencias"]):
                for bi, b in enumerate(exp.get("bullets") or [], 1):
                    titulo_lg = b.split(":", 1)[0][:110].strip()
                    if titulo_lg.lower().startswith(("titular ", "etapa ", "no incluir", "versión ")):
                        continue
                    sks = [
                        s.lower() for s in perfil["skills"]["avanzado"]
                        if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", b.lower())
                    ]
                    m_met = re.search(r"(~?\$[\d,\.]+(?:\+?\s*USD)?(?:/(?:mes|año|mo|yr))?|\-?\+?\d+%|\~?[\d,]+\s*llamadas)", b, re.I)
                    logros_loaded.append({
                        "id": f"logro:{slugify(titulo_lg, f'hist-{idx}-{bi}')}",
                        "titulo": titulo_lg,
                        "angulo": _inferir_angulo_texto(b),
                        "ahorro": m_met.group(1) if m_met else exp.get("empresa", "Logro verificado"),
                        "skills": sks[:8],
                        "metricas": b[:180],
                        "descripcion": b[:260],
                    })
            perfil["impact_log"] = logros_loaded[:14]
            perfil["sintetizado_por"] = "perfil-maestro.md histórico (detectado automáticamente)"

    # 2. Leer perfil/preferencias.md si es real
    if PREF_MD_PATH.is_file():
        pref_txt = PREF_MD_PATH.read_text(encoding="utf-8", errors="ignore")
        _parsear_preferencias_md(pref_txt, perfil["preferencias"])

    if not perfil["preferencias"].get("pais_residencia") and dp.get("pais"):
        perfil["preferencias"]["pais_residencia"] = dp["pais"]

    # 3. Auto-detectar CV Base ya existente en cvs-generados/base/
    if CV_BASE_DIR.is_dir():
        pdfs = sorted(CV_BASE_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pdfs:
            top = pdfs[0]
            perfil["cv_base"] = {
                "modo": "historico",
                "ruta": str(top.relative_to(ROOT).as_posix()),
                "doc_id": None,
                "nombre_archivo": top.name,
            }

    return perfil


def cargar_perfil_estructurado() -> dict:
    if STRUCTURED_PATH.is_file():
        try:
            raw = json.loads(STRUCTURED_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                merged = _deep_merge(DEFAULT_PERFIL, raw)
                # Si aún no tiene cv_base pero existe uno histórico en cvs-generados/base/, vincularlo
                if not (merged.get("cv_base") or {}).get("ruta") and CV_BASE_DIR.is_dir():
                    pdfs = sorted(CV_BASE_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if pdfs:
                        merged["cv_base"] = {
                            "modo": "historico",
                            "ruta": str(pdfs[0].relative_to(ROOT).as_posix()),
                            "doc_id": None,
                            "nombre_archivo": pdfs[0].name,
                        }
                return merged
        except Exception:
            pass
    return _seed_desde_markdown_existente()


def calcular_completitud(perfil: dict, docs: list[dict] | None = None) -> dict:
    docs = docs if docs is not None else _cargar_indice_docs()
    historicas = detectar_fuentes_historicas()
    dp = perfil.get("datos_personales") or {}
    pref = perfil.get("preferencias") or {}
    checks = {
        "fuentes": bool(
            docs
            or historicas
            or (perfil.get("historia_libre") or "").strip()
            or (perfil.get("urls_referencia") or [])
        ),
        "identidad": bool((dp.get("nombre") or "").strip() and (perfil.get("titular_principal") or perfil.get("resumen_profesional"))),
        "preferencias": bool((pref.get("puestos_objetivo") or []) or (pref.get("areas") or [])),
        "experiencia_o_skills": bool(
            (perfil.get("experiencias") or [])
            or (perfil.get("impact_log") or [])
            or (perfil.get("skills", {}).get("avanzado") or [])
        ),
        "cv_base": bool((perfil.get("cv_base") or {}).get("ruta")),
    }
    score = int(round((sum(1 for v in checks.values() if v) / len(checks)) * 100))
    return {
        "porcentaje": score,
        "checks": checks,
        "listo_para_match": checks["identidad"] and (checks["fuentes"] or checks["experiencia_o_skills"]),
    }


def guardar_perfil_estructurado(perfil: dict, regenerar_md: bool = True) -> dict:
    merged = _deep_merge(DEFAULT_PERFIL, perfil)
    merged["actualizado"] = dt.date.today().isoformat()

    # Normalizar slugs de áreas
    areas_clean = []
    seen_slugs: set[str] = set()
    for a in merged.get("preferencias", {}).get("areas") or []:
        if not isinstance(a, dict):
            continue
        nombre = (a.get("nombre") or a.get("slug") or "").strip()
        if not nombre:
            continue
        sl = slugify(a.get("slug") or nombre)
        if sl in seen_slugs:
            continue
        seen_slugs.add(sl)
        kws = [str(k).strip() for k in (a.get("keywords") or []) if str(k).strip()]
        areas_clean.append({"slug": sl, "nombre": nombre, "keywords": kws})
    merged["preferencias"]["areas"] = areas_clean

    # Normalizar IDs del impact_log
    logros_clean = []
    for i, lg in enumerate(merged.get("impact_log") or [], 1):
        if not isinstance(lg, dict) or not (lg.get("titulo") or "").strip():
            continue
        lid = (lg.get("id") or "").strip()
        if not lid.startswith("logro:"):
            lid = f"logro:{slugify(lg['titulo'], fallback=f'item-{i}')}"
        logros_clean.append(
            {
                "id": lid,
                "titulo": lg["titulo"].strip(),
                "angulo": slugify(lg.get("angulo") or (areas_clean[0]["slug"] if areas_clean else "base")),
                "ahorro": (lg.get("ahorro") or lg.get("metricas") or "Impacto verificado").strip(),
                "skills": [str(s).strip().lower() for s in (lg.get("skills") or []) if str(s).strip()],
                "metricas": (lg.get("metricas") or lg.get("ahorro") or "").strip(),
                "descripcion": (lg.get("descripcion") or lg["titulo"]).strip(),
            }
        )
    merged["impact_log"] = logros_clean

    PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    STRUCTURED_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    if regenerar_md:
        PERFIL_MD_PATH.write_text(render_perfil_maestro_md(merged), encoding="utf-8")
        PREF_MD_PATH.write_text(render_preferencias_md(merged), encoding="utf-8")

    return merged


# ------------------------------------------------------- Markdown Generators
def render_perfil_maestro_md(perfil: dict) -> str:
    dp = perfil.get("datos_personales") or {}
    nombre = dp.get("nombre") or "Candidato"
    ubicacion = dp.get("ubicacion") or dp.get("pais") or "Por definir"
    if dp.get("zona_horaria") and dp["zona_horaria"] not in ubicacion:
        ubicacion = f"{ubicacion} ({dp['zona_horaria']})"

    lines = [
        f"# Perfil Maestro — {nombre}",
        "",
        f"> **Generado/actualizado:** {perfil.get('actualizado') or dt.date.today().isoformat()} · Fuente canónica sincronizada con `perfil/perfil-estructurado.json`.",
        "> **Regla de Veracidad Estricta:** Solo contiene experiencia, logros y certificaciones declaradas por el usuario.",
        "",
        "## 1. Datos de Identificación y Contacto",
        "",
        f"- **Nombre completo:** {nombre}",
        f"- **Ubicación base:** {ubicacion}",
    ]
    if dp.get("email"):
        lines.append(f"- **Email:** `{dp['email']}`")
    if dp.get("telefono"):
        lines.append(f"- **Teléfono:** `{dp['telefono']}`")
    if dp.get("linkedin"):
        lines.append(f"- **LinkedIn:** `{dp['linkedin']}`")
    if dp.get("github"):
        lines.append(f"- **GitHub:** `{dp['github']}`")
    if dp.get("portafolio"):
        lines.append(f"- **Portafolio:** `{dp['portafolio']}`")

    idiomas = dp.get("idiomas") or []
    if idiomas:
        lines.append("- **Idiomas:**")
        for idm in idiomas:
            if isinstance(idm, dict) and idm.get("idioma"):
                lines.append(f"  - **{idm['idioma']}:** {idm.get('nivel') or 'Intermedio'}")

    lines.extend(["", "## 2. Posicionamiento Estratégico", ""])
    if perfil.get("titular_principal"):
        lines.append(f"- **Titular Principal:** `{perfil['titular_principal']}`")
    if perfil.get("resumen_profesional"):
        lines.extend(["", perfil["resumen_profesional"]])

    skills = perfil.get("skills") or {}
    lines.extend(["", "## 3. Stack Técnico y Competencias", ""])
    if skills.get("avanzado"):
        lines.append(f"- 🟢 **Dominado / Producción:** {', '.join(skills['avanzado'])}")
    if skills.get("intermedio"):
        lines.append(f"- 🟡 **Intermedio / Práctico:** {', '.join(skills['intermedio'])}")
    if skills.get("aprendiendo"):
        lines.append(f"- 🔴 **En aprendizaje / Objetivo:** {', '.join(skills['aprendiendo'])}")
    if not any(skills.get(k) for k in ("avanzado", "intermedio", "aprendiendo")):
        lines.append("- *(Pendiente de completar o sintetizar desde documentos)*")

    lines.extend(["", "## 4. Experiencia Profesional", ""])
    exps = perfil.get("experiencias") or []
    if exps:
        for exp in exps:
            emp = exp.get("empresa") or "Empresa"
            meta = " | ".join(p for p in [exp.get("rubro"), exp.get("periodo"), exp.get("ubicacion")] if p)
            lines.append(f"### **{emp}**" + (f" *({meta})*" if meta else ""))
            if exp.get("cargo"):
                lines.append(f"- **Rol:** {exp['cargo']}")
            for b in exp.get("bullets") or []:
                lines.append(f"- {b}")
            lines.append("")
    else:
        lines.append("- *(Agrega tus experiencias o sube tu CV/LinkedIn PDF para sintetizarlas)*\n")

    lines.extend(["## 5. Impact Log (Logros Verificados para Neo4j GraphRAG)", ""])
    logros = perfil.get("impact_log") or []
    if logros:
        for lg in logros:
            sks = ", ".join(lg.get("skills") or [])
            lines.append(
                f"- **{lg['titulo']}** *(Ángulo: `{lg.get('angulo', 'base')}` | Impacto: {lg.get('ahorro', '-')})* — {lg.get('descripcion', '')}"
                + (f" `[Skills: {sks}]`" if sks else "")
            )
    else:
        lines.append("- *(Sin logros estructurados aún)*")

    lines.extend(["", "## 6. Educación y Certificaciones", ""])
    for ed in perfil.get("educacion") or []:
        if isinstance(ed, dict) and ed.get("titulo"):
            per = f" ({ed['periodo']})" if ed.get("periodo") else ""
            inst = f" — {ed['institucion']}" if ed.get("institucion") else ""
            lines.append(f"- **{ed['titulo']}**{inst}{per}")
    for cert in perfil.get("certificaciones") or []:
        if isinstance(cert, dict) and cert.get("nombre"):
            ent = f" — {cert['entidad']}" if cert.get("entidad") else ""
            fec = f" ({cert['fecha']})" if cert.get("fecha") else ""
            lines.append(f"- **Certificación:** {cert['nombre']}{ent}{fec}")

    if (perfil.get("historia_libre") or "").strip():
        lines.extend(["", "## 7. Historia y Contexto Personal (Declarado por el Usuario)", "", perfil["historia_libre"].strip()])

    lines.append("")
    return "\n".join(lines)


def render_preferencias_md(perfil: dict) -> str:
    pref = perfil.get("preferencias") or {}
    sal = pref.get("salario") or {}
    rub = pref.get("rubrica") or {}
    mod_lbl = MODALIDAD_LABELS.get(pref.get("modalidad", "remoto_100"), pref.get("modalidad", "100% Remoto"))
    sen_lbl = SENIORITY_LABELS.get(pref.get("seniority_objetivo", "mid"), pref.get("seniority_objetivo", "Mid-Level"))
    pais = pref.get("pais_residencia") or perfil.get("datos_personales", {}).get("pais") or "No especificado"
    regiones = ", ".join(pref.get("regiones_aceptadas") or ["Global"])

    lines = [
        "# Preferencias de Búsqueda, No Negociables y Rúbrica de Match",
        "",
        f"> **Actualizado:** {perfil.get('actualizado') or dt.date.today().isoformat()} · Sincronizado con `perfil/perfil-estructurado.json`.",
        "",
        "## 1. Condiciones No Negociables y Geografía",
        "",
        f"1. **Modalidad aceptada:** {mod_lbl}",
        f"2. **País de residencia:** {pais}",
        f"3. **Regiones / Elegibilidad aceptada:** {regiones}",
        f"4. **Seniority objetivo:** {sen_lbl} (~{pref.get('anos_experiencia', 2)} años de experiencia)",
        f"5. **Tipo de rol:** {'Individual Contributor (IC) técnico — sin roles de gestión/Manager' if pref.get('solo_ic', True) else 'Abierto a IC y roles de liderazgo/gestión'}",
        f"6. **Idiomas de trabajo:** {', '.join(pref.get('idiomas_trabajo') or ['Español'])}",
    ]

    if pref.get("no_negociables"):
        lines.extend(["", "### Otros No Negociables", ""])
        for item in pref["no_negociables"]:
            lines.append(f"- {item}")

    if pref.get("exclusiones"):
        lines.extend(["", "### Exclusiones Duras (Descarte Automático)", ""])
        for ex in pref["exclusiones"]:
            lines.append(f"- `{ex}`")

    lines.extend(["", "## 2. Puestos y Áreas Objetivo", ""])
    if pref.get("puestos_objetivo"):
        lines.append("### Roles buscados")
        for r in pref["puestos_objetivo"]:
            lines.append(f"- `{r}`")
        lines.append("")

    if pref.get("areas"):
        lines.append("### Áreas / Ángulos de Especialización")
        for a in pref["areas"]:
            kws = ", ".join(a.get("keywords") or [])
            lines.append(f"- **{a['nombre']}** (`{a['slug']}`): {kws}")
        lines.append("")

    lines.extend(["## 3. Metas de Compensación", ""])
    mon = sal.get("moneda") or "USD"
    per = sal.get("periodo") or "mes"
    min_s = sal.get("minimo") or 0
    ideal_s = sal.get("ideal") or 0
    if min_s or ideal_s:
        lines.append(f"- **Mínimo no negociable:** `{mon} {min_s:,}/{per}`" if min_s else "- **Mínimo:** Por definir")
        lines.append(f"- **Meta ideal:** `{mon} {ideal_s:,}/{per}`" if ideal_s else "- **Meta ideal:** Por definir")
    else:
        lines.append("- **Rango salarial:** Por confirmar / abierto a propuestas competitivas")

    lines.extend(
        [
            "",
            "## 4. Rúbrica de Puntuación (0–100)",
            "",
            "| Criterio | Peso Máx. |",
            "| :--- | :---: |",
            f"| 1. Alineación Técnica / Dominio Core | {rub.get('tecnico', 35)} pts |",
            f"| 2. Modalidad y Elegibilidad Geográfica | {rub.get('modalidad', 20)} pts |",
            f"| 3. Idioma y Comunicación | {rub.get('idioma', 15)} pts |",
            f"| 4. Banda Salarial / Tipo de Empresa | {rub.get('salario', 15)} pts |",
            f"| 5. Calce de Seniority y Factibilidad | {rub.get('seniority', 15)} pts |",
            "",
        ]
    )
    return "\n".join(lines)


# -------------------------------------------------------- AI Synthesis Engine
def _recopilar_corpus_usuario(perfil: dict) -> str:
    partes: list[str] = []
    # 1. Documentos subidos en la bóveda
    docs = _cargar_indice_docs()
    for d in docs:
        txt_path = ROOT / d.get("ruta_texto", "")
        if txt_path.is_file():
            txt = txt_path.read_text(encoding="utf-8", errors="ignore").strip()
            if txt:
                partes.append(
                    f"--- DOCUMENTO [{d.get('categoria', 'otro').upper()}]: {d.get('nombre_original')} ---\n{txt[:9000]}"
                )

    # 2. Fuentes históricas reales ya existentes en el repositorio
    if PERFIL_MD_PATH.is_file():
        txt_p = PERFIL_MD_PATH.read_text(encoding="utf-8", errors="ignore").strip()
        if txt_p and not _es_plantilla_ejemplo(txt_p):
            partes.append(f"--- PERFIL MAESTRO HISTÓRICO (perfil/perfil-maestro.md) ---\n{txt_p[:12000]}")
    if PREF_MD_PATH.is_file():
        txt_pr = PREF_MD_PATH.read_text(encoding="utf-8", errors="ignore").strip()
        if txt_pr and not _es_plantilla_ejemplo(txt_pr):
            partes.append(f"--- PREFERENCIAS HISTÓRICAS (perfil/preferencias.md) ---\n{txt_pr[:6000]}")

    for u in perfil.get("urls_referencia") or []:
        if u.get("texto_extraido"):
            partes.append(
                f"--- URL [{u.get('tipo', 'web').upper()}]: {u.get('url')} ---\n{u['texto_extraido'][:4500]}"
            )

    if (perfil.get("historia_libre") or "").strip():
        partes.append(f"--- HISTORIA CONTADA POR EL USUARIO ---\n{perfil['historia_libre'].strip()[:8000]}")

    return "\n\n".join(partes)[:32000]


def _extraccion_heuristica_sin_ia(perfil: dict, corpus: str) -> dict:
    """Fallback determinista cuando aún no hay API key: extrae emails, links y skills básicos
    del texto subido sin inventar nada."""
    out = copy.deepcopy(perfil)
    dp = out["datos_personales"]

    if not dp.get("email"):
        m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", corpus)
        if m:
            dp["email"] = m.group(0)
    if not dp.get("linkedin"):
        m = re.search(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_%\-]+", corpus, re.I)
        if m:
            dp["linkedin"] = m.group(0)
    if not dp.get("github"):
        m = re.search(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_\-]+", corpus, re.I)
        if m:
            dp["github"] = m.group(0)
    if not dp.get("telefono"):
        m = re.search(r"\+\d{1,3}[\s\-]?\d[\d\s\-]{7,13}", corpus)
        if m:
            dp["telefono"] = m.group(0).strip()

    # Detectar skills comunes presentes literalmente en el corpus
    catalogo_skills = [
        "Python", "JavaScript", "TypeScript", "SQL", "Java", "Go", "C#", "React", "Node.js",
        "FastAPI", "Django", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Docker", "Kubernetes",
        "AWS", "Azure", "GCP", "Terraform", "Ansible", "Linux", "Git", "CI/CD", "GitHub Actions",
        "FinOps", "DevSecOps", "SecOps", "SIEM", "Wazuh", "GuardDuty", "IAM", "Power BI",
        "Tableau", "dbt", "Airflow", "Spark", "Pandas", "Machine Learning", "LLMs", "n8n",
        "Figma", "Product Management", "Scrum", "Jira", "Salesforce", "HubSpot", "Excel",
    ]
    detectados = [
        sk for sk in catalogo_skills
        if re.search(r"(?<![a-z0-9])" + re.escape(sk.lower()) + r"(?![a-z0-9])", corpus.lower())
    ]
    if detectados and not out["skills"]["avanzado"]:
        out["skills"]["avanzado"] = detectados[:18]

    if not out.get("resumen_profesional") and (out.get("historia_libre") or "").strip():
        out["resumen_profesional"] = out["historia_libre"].strip()[:500]

    preguntas = []
    if not dp.get("nombre"):
        preguntas.append("¿Cuál es tu nombre completo para el encabezado del CV?")
    if not out.get("preferencias", {}).get("puestos_objetivo"):
        preguntas.append("¿Qué puestos de trabajo exactos estás buscando?")
    if not out.get("impact_log"):
        preguntas.append("Configura una API key en 'API Keys & IA' para que la IA extraiga automáticamente tu experiencia y logros cuantificados desde tus documentos.")
    out["preguntas_faltantes"] = preguntas
    out["sintetizado_por"] = "heuristica (sin API key)"
    return out


def sintetizar_perfil_con_ia() -> dict:
    """Lee todos los documentos subidos, URLs, historia libre y preferencias del usuario
    y sintetiza un perfil estructurado completo con veracidad estricta."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import evaluador_ia

    perfil_actual = cargar_perfil_estructurado()
    corpus = _recopilar_corpus_usuario(perfil_actual)

    if not corpus.strip():
        raise ValueError(
            "Aún no has ingresado documentos, URLs ni tu historia profesional. "
            "Sube un PDF (LinkedIn o CV) o cuéntanos tu trayectoria primero."
        )

    prov = evaluador_ia.detectar_proveedor_ia(rol="perfil-tracker")
    if prov["provider"] == "heuristica":
        nuevo = _extraccion_heuristica_sin_ia(perfil_actual, corpus)
        guardado = guardar_perfil_estructurado(nuevo, regenerar_md=True)
        return {"perfil": guardado, "provider": "heuristica", "model": "sin-api-key", "modo": "heuristica"}

    pref_actual = json.dumps(perfil_actual.get("preferencias") or {}, ensure_ascii=False, indent=2)
    dp_actual = json.dumps(perfil_actual.get("datos_personales") or {}, ensure_ascii=False, indent=2)

    prompt = f"""Eres un especialista en Perfilamiento Profesional y ATS (Career Agent).
Tu tarea es leer TODA la documentación real proporcionada por el usuario (PDF de LinkedIn, CV previo, certificados, portafolio y su historia contada en sus propias palabras) y construir su Perfil Maestro estructurado.

REGLA DE VERACIDAD ESTRICTA (OBLIGATORIA):
- NUNCA inventes empresas, fechas, títulos, certificaciones, idiomas, métricas, ahorros ni tecnologías que NO aparezcan explícitamente en las fuentes del usuario.
- Si un dato falta (ej. teléfono, métricas numéricas, nivel de inglés), déjalo vacío y agrégalo en `preguntas_faltantes` para preguntarle al usuario.
- Conserva cualquier preferencia o dato personal ya declarado explícitamente por el usuario; solo completa los campos vacíos o enriquece áreas/keywords sugeridas si el usuario no definió ninguna.

=== DATOS PERSONALES ACTUALES DEL USUARIO ===
{dp_actual}

=== PREFERENCIAS Y NO NEGOCIABLES ACTUALES DEL USUARIO ===
{pref_actual}

=== DOCUMENTACIÓN E HISTORIA REAL DEL USUARIO ===
{corpus}

INSTRUCCIONES DE SALIDA:
Responde ÚNICAMENTE con un objeto JSON válido (sin markdown ni texto fuera del JSON) con esta estructura exacta:
{{
  "datos_personales": {{
    "nombre": "Nombre completo real o ''",
    "email": "email real o ''",
    "telefono": "teléfono real o ''",
    "ubicacion": "Ciudad, País",
    "pais": "País de residencia",
    "zona_horaria": "ej. CLT / EST / CET o ''",
    "linkedin": "URL real o ''",
    "github": "URL real o ''",
    "portafolio": "URL real o ''",
    "idiomas": [{{"idioma": "Español", "nivel": "Nativo"}}]
  }},
  "titular_principal": "Titular profesional conciso orientado a ATS (1 línea, basado en su perfil y puestos objetivo)",
  "resumen_profesional": "Resumen profesional de 3-4 líneas de alto impacto basado SOLO en hechos verificables del usuario.",
  "skills": {{
    "avanzado": ["lista de 8-20 tecnologías/competencias demostradas en experiencia real"],
    "intermedio": ["lista de herramientas con uso práctico/intermedio"],
    "aprendiendo": ["lista de skills que está aprendiendo o quiere desarrollar"]
  }},
  "experiencias": [
    {{
      "empresa": "Nombre real de la empresa",
      "cargo": "Cargo exacto",
      "periodo": "Ej. 2023 – Presente",
      "ubicacion": "Ej. Remoto / Ciudad",
      "rubro": "Industria si se menciona o ''",
      "bullets": ["3-6 bullets de logros concretos redactados estilo ATS (verbo de acción + resultado real)"]
    }}
  ],
  "impact_log": [
    {{
      "titulo": "Título corto del logro clave verificable",
      "angulo": "slug de una de las áreas del usuario (minúsculas con guiones)",
      "ahorro": "métrica/resultado real citado por el usuario (o 'Impacto operativo' si no dio cifra)",
      "skills": ["3-7 skills concretos en minúsculas que demuestra este logro"],
      "metricas": "detalle cuantitativo o resultado real",
      "descripcion": "1-2 frases explicando el logro"
    }}
  ],
  "educacion": [
    {{"titulo": "Título / Carrera", "institucion": "Universidad / Instituto", "periodo": "Año o rango"}}
  ],
  "certificaciones": [
    {{"nombre": "Nombre certificación", "entidad": "Emisor", "fecha": "Año o ''"}}
  ],
  "preferencias_sugeridas": {{
    "puestos_objetivo": ["3-6 puestos objetivo coherentes con lo que busca y su perfil"],
    "areas": [
      {{"slug": "slug-corto", "nombre": "Nombre del Área", "keywords": ["5-10 keywords clave para buscar ofertas"]}}
    ],
    "anos_experiencia": 3,
    "seniority_objetivo": "junior | mid | senior | lead | manager"
  }},
  "preguntas_faltantes": [
    "Máximo 5 preguntas concretas y útiles sobre datos faltantes que mejorarían su CV y el match (ej. métricas cuantificables, nivel de inglés, expectativas salariales)."
  ]
}}
"""
    raw = evaluador_ia.llamar_llm_json(prompt, rol="perfil-tracker")
    sint = copy.deepcopy(perfil_actual)

    # Mezclar datos personales sin borrar campos que el usuario ya rellenó explícitamente
    dp_in = raw.get("datos_personales") or {}
    for k in ("nombre", "email", "telefono", "ubicacion", "pais", "zona_horaria", "linkedin", "github", "portafolio"):
        val = (dp_in.get(k) or "").strip()
        if val and not (sint["datos_personales"].get(k) or "").strip():
            sint["datos_personales"][k] = val
    if isinstance(dp_in.get("idiomas"), list) and dp_in["idiomas"]:
        sint["datos_personales"]["idiomas"] = dp_in["idiomas"]

    if (raw.get("titular_principal") or "").strip():
        sint["titular_principal"] = raw["titular_principal"].strip()
    if (raw.get("resumen_profesional") or "").strip():
        sint["resumen_profesional"] = raw["resumen_profesional"].strip()

    if isinstance(raw.get("skills"), dict):
        for lvl in ("avanzado", "intermedio", "aprendiendo"):
            vals = raw["skills"].get(lvl)
            if isinstance(vals, list) and vals:
                sint["skills"][lvl] = [str(s).strip() for s in vals if str(s).strip()]

    if isinstance(raw.get("experiencias"), list) and raw["experiencias"]:
        sint["experiencias"] = raw["experiencias"]
    if isinstance(raw.get("impact_log"), list) and raw["impact_log"]:
        sint["impact_log"] = raw["impact_log"]
    if isinstance(raw.get("educacion"), list) and raw["educacion"]:
        sint["educacion"] = raw["educacion"]
    if isinstance(raw.get("certificaciones"), list) and raw["certificaciones"]:
        sint["certificaciones"] = raw["certificaciones"]

    # Preferencias sugeridas: solo llenar si el usuario aún no definió las suyas
    psug = raw.get("preferencias_sugeridas") or {}
    pref = sint["preferencias"]
    if not pref.get("puestos_objetivo") and isinstance(psug.get("puestos_objetivo"), list):
        pref["puestos_objetivo"] = [str(x).strip() for x in psug["puestos_objetivo"] if str(x).strip()]
    if not pref.get("areas") and isinstance(psug.get("areas"), list):
        pref["areas"] = psug["areas"]
    if not pref.get("pais_residencia") and sint["datos_personales"].get("pais"):
        pref["pais_residencia"] = sint["datos_personales"]["pais"]
    if isinstance(psug.get("anos_experiencia"), (int, float)) and not perfil_actual["preferencias"].get("anos_experiencia"):
        pref["anos_experiencia"] = int(psug["anos_experiencia"])

    sint["preguntas_faltantes"] = [str(q).strip() for q in (raw.get("preguntas_faltantes") or []) if str(q).strip()][:5]
    sint["sintetizado_por"] = f"{prov['provider']}/{prov['model']}"

    guardado = guardar_perfil_estructurado(sint, regenerar_md=True)
    return {"perfil": guardado, "provider": prov["provider"], "model": prov["model"], "modo": "ia"}
