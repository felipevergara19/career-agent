#!/usr/bin/env python3
"""Motor de diseño vanguardista 1-página para CVs usando Typst 0.15 + fuentes embebidas.

Arquitecturas visuales por ángulo (`DEFAULT_LAYOUT_BY_ANGULO`):
  - `finops`        -> `editorial`   (Swiss Editorial Split Sidebar — Esmeralda + Grafito)
  - `ai-automation` -> `bento`       (Bento KPI Dashboard + Timeline — Índigo + Cian)
  - `devsecops`     -> `editorial`   (Swiss Editorial Split Sidebar — Navy Obsidiana + Ámbar)
  - `secops-siem`   -> `tech-banner` (Linear Tech Banner + 2×2 Skill Grid — Grafito + Carmesí)
  - `base`          -> `editorial`   (Swiss Editorial Split Sidebar — Esmeralda + Grafito)

100% agnóstico al usuario (cero datos personales embebidos): extrae contacto,
métricas KPI, experiencia, skills, educación, certificaciones e idiomas directamente
del CV Markdown (y opcionalmente de un payload JSON estructurado).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

import rendercv_fonts
import typst

FONT_PATHS = [str(p) for p in rendercv_fonts.paths_to_font_folders]

DEFAULT_LAYOUT_BY_ANGULO: dict[str, str] = {
    "finops": "editorial",
    "ai-automation": "bento",
    "devsecops": "editorial",
    "secops-siem": "tech-banner",
    "base": "editorial",
}

THEME_PALETTES: dict[str, dict[str, str]] = {
    "finops": {
        "dark_bg": "#0B1F1D",
        "dark_bg_alt": "#112E2A",
        "accent": "#059669",
        "accent_bright": "#34D399",
        "accent_soft": "#ECFDF5",
        "accent_border": "#A7F3D0",
        "text": "#111827",
        "muted": "#4B5563",
        "surface": "#F8FAFC",
        "border": "#E2E8F0",
        "badge_label": "CLOUD FINOPS · COST OPTIMIZATION",
        "font_display": "Poppins",
        "font_body": "Lato",
    },
    "ai-automation": {
        "dark_bg": "#0F102A",
        "dark_bg_alt": "#1B1D46",
        "accent": "#4F46E5",
        "accent_bright": "#38BDF8",
        "accent_soft": "#EEF2FF",
        "accent_border": "#C7D2FE",
        "text": "#111827",
        "muted": "#4B5563",
        "surface": "#F8FAFC",
        "border": "#E2E8F0",
        "badge_label": "AI AUTOMATION · LLMs & PYTHON",
        "font_display": "Open Sauce Sans",
        "font_body": "Source Sans 3",
    },
    "devsecops": {
        "dark_bg": "#0C1829",
        "dark_bg_alt": "#162844",
        "accent": "#D97706",
        "accent_bright": "#FBBF24",
        "accent_soft": "#FFFBEB",
        "accent_border": "#FDE68A",
        "text": "#0F172A",
        "muted": "#475569",
        "surface": "#F8FAFC",
        "border": "#E2E8F0",
        "badge_label": "DEVSECOPS · CLOUD INFRASTRUCTURE",
        "font_display": "Raleway",
        "font_body": "Source Sans 3",
    },
    "secops-siem": {
        "dark_bg": "#15131B",
        "dark_bg_alt": "#261724",
        "accent": "#BE123C",
        "accent_bright": "#FB7185",
        "accent_soft": "#FFF1F2",
        "accent_border": "#FECDD3",
        "text": "#111827",
        "muted": "#4B5563",
        "surface": "#F9FAFB",
        "border": "#E5E7EB",
        "badge_label": "CLOUD SECURITY · SECOPS & SIEM",
        "font_display": "Ubuntu",
        "font_body": "Open Sans",
    },
}

LAYOUT_META: dict[str, dict[str, Any]] = {
    "bento": {
        "titulo": "Bento KPI Dashboard + Timeline (1 pág.)",
        "subtitulo": "Hero oscuro full-bleed, tarjetas KPI, timeline vertical y sidebar con pills de skills, certificaciones y barras de idioma.",
        "ats_nota": "1 página · Alto impacto visual",
    },
    "editorial": {
        "titulo": "Swiss Editorial Split Sidebar (1 pág.)",
        "subtitulo": "Sidebar izquierdo full-bleed oscuro con monograma, contacto, stack en chips, certificaciones e idiomas + lienzo blanco editorial.",
        "ats_nota": "1 página · Diseño editorial / Hiring Manager",
    },
    "tech-banner": {
        "titulo": "Linear Tech Banner + 2×2 Skill Grid (1 pág.)",
        "subtitulo": "Header asimétrico oscuro, cinta de métricas KPI, grilla 2×2 de skills en pills y experiencia full-width en flujo lineal.",
        "ats_nota": "1 página · Flujo lineal 100% ATS estricto",
    },
}

I18N_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "contact": "Contact",
        "tech_stack": "Tech Stack",
        "core_tech_stack": "Core Tech Stack",
        "core_technical_stack": "Core Technical Stack",
        "soft_skills": "Soft Skills",
        "certifications": "Certifications",
        "certifications_languages": "Certifications & Languages",
        "flagship_cert": "CERTIFICATION",
        "education": "Education",
        "languages": "Languages",
        "executive_profile": "Executive Profile",
        "executive_summary": "Executive Summary",
        "experience": "Professional Experience",
        "project": "Featured Open-Source Project",
        "project_arch": "Featured Open-Source Architecture",
        "bottom_sec": "Education, Certifications & Soft Skills",
        "open_source": "OPEN-SOURCE",
    },
    "es": {
        "contact": "Contacto",
        "tech_stack": "Stack Técnico",
        "core_tech_stack": "Stack Técnico",
        "core_technical_stack": "Competencias Técnicas",
        "soft_skills": "Habilidades Blandas",
        "certifications": "Certificaciones",
        "certifications_languages": "Certificaciones e Idiomas",
        "flagship_cert": "CERTIFICACIÓN",
        "education": "Educación",
        "languages": "Idiomas",
        "executive_profile": "Perfil Ejecutivo",
        "executive_summary": "Resumen Ejecutivo",
        "experience": "Experiencia Profesional",
        "project": "Proyecto Open-Source Destacado",
        "project_arch": "Arquitectura Open-Source Destacada",
        "bottom_sec": "Educación, Certificaciones y Habilidades Blandas",
        "open_source": "OPEN-SOURCE",
    },
}


def typst_escape(text: str) -> str:
    """Escapa texto plano para Typst preservando **negrita**, *cursiva* y `código`."""
    s = str(text or "")
    s = s.replace("\\", "\\\\")
    for ch in ("#", "$", "@", "<", ">", "~", "_", "[", "]", ";"):
        s = s.replace(ch, f"\\{ch}")

    s = re.sub(r"`([^`]+)`", lambda m: f"#raw({typst_str(m.group(1).replace('\\\\', '\\'))})", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"#strong[\1]", s)
    s = re.sub(r"\*([^*]+)\*", r"#emph[\1]", s)
    return s


def typst_str(text: str) -> str:
    return '"' + str(text or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _strip_md_stars(text: str) -> str:
    return re.sub(r"\*\*|\*", "", str(text or "")).strip()


def _render_skill_pills_typst(items: list[str], style: str = "light") -> str:
    fn = "pill-dark" if style == "dark" else "pill-light"
    return " ".join(f"#{fn}[{typst_escape(it)}]" for it in items)


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", (name or "CV").strip()) if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return (parts[0][:2] if parts else "CV").upper()


def _safe_trim(text: str, max_len: int) -> str:
    """Recorta texto solo en límite de palabra para nunca dejar palabras cortadas."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:-·(")
    return cut if cut else s[:max_len]


def _clean_location(raw: str, idioma: str) -> str:
    """Limpia ubicaciones largas con paréntesis extensos sin cortar palabras."""
    s = _strip_md_stars(raw).strip()
    if not s:
        return ""
    if len(s) <= 48:
        return s
    # Extraer ciudad/país antes del paréntesis largo
    base = s.split("(", 1)[0].strip(" ,·|-")
    tz = re.search(r"\b(CLT\s*/\s*EST|EST\s*/\s*CST|CLT|EST|CST|UTC[+-]\d+)\b", s)
    remote = "100% Remoto" if idioma == "es" else "100% Remote"
    parts = [p for p in (base, remote if "remot" in s.lower() else "", f"({tz.group(1)})" if tz else "") if p]
    return " · ".join(parts[:2]) + (f" {parts[2]}" if len(parts) > 2 else "") if parts else _safe_trim(s, 48)


def _split_skill_tags(raw: str, max_tags: int = 12) -> list[str]:
    """Divide una cadena de skills separadas por coma respetando paréntesis, sin truncar palabras ni duplicar."""
    tags: list[str] = []
    seen: set[str] = set()
    buf: list[str] = []
    depth = 0
    for ch in raw:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            item = _strip_md_stars("".join(buf)).strip(" .;")
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                tags.append(item)
            buf = []
        else:
            buf.append(ch)
    last = _strip_md_stars("".join(buf)).strip(" .;")
    if last and last.lower() not in seen:
        tags.append(last)
    return tags[:max_tags]


def _extract_kpis_from_text(
    summary: str,
    experiences: list[dict[str, Any]],
    certifications: list[dict[str, str]],
    languages: list[dict[str, Any]],
    idioma: str,
) -> list[dict[str, str]]:
    """Extrae hasta 4 KPIs reales solo cuando el layout los requiere, sin cortar palabras."""
    kpis: list[dict[str, str]] = []
    seen_values: set[str] = set()

    metric_re = re.compile(
        r"([~≈−\-+]?\$[\d,.]+[kKmM]?\+?(?:/(?:yr|year|año|mo|month|mes))?|"
        r"[~≈−\-+]?\d+(?:[.,]\d+)?%|"
        r"[~≈]?\d[\d,.]*\+?\s*(?:calls/day|llamadas(?:/día|\s+diarias)?|APIs))"
    )

    pool: list[str] = [summary]
    for exp in experiences:
        pool.extend(exp.get("highlights") or [])

    for sentence in pool:
        clean = _strip_md_stars(sentence)
        m = metric_re.search(clean)
        if not m:
            continue
        val = m.group(1).strip()
        norm = val.lower().replace(" ", "")
        if norm in seen_values or len(val) > 16:
            continue
        seen_values.add(norm)
        head = clean.split(":", 1)[0] if ":" in clean[:70] else clean
        label = _safe_trim(head, 24)
        sub_src = re.sub(r"\s+", " ", clean.split(":", 1)[-1] if ":" in clean[:70] else clean)
        sub = _safe_trim(sub_src.replace(val, "").strip(" :,-·()"), 34)
        kpis.append({"value": val, "label": label or ("Impact" if idioma == "en" else "Impacto"), "sub": sub})
        if len(kpis) >= 3:
            break

    if len(kpis) < 4 and certifications:
        c0 = certifications[0]
        short = re.search(r"\(([A-Z0-9\-]{2,10})\)", c0["name"])
        val = short.group(1) if short else _safe_trim(c0["name"], 14)
        kpis.append({
            "value": val,
            "label": "Certified" if idioma == "en" else "Certificado",
            "sub": _safe_trim(c0["name"], 34),
        })

    return kpis[:4]


def _contact_line_typst(data: dict[str, Any], sep: str = "·", gap: str = "5pt") -> str:
    parts: list[str] = []
    if data.get("location"):
        parts.append(typst_escape(data["location"]))
    if data.get("email"):
        em = data["email"]
        parts.append(f'#link("mailto:{em}")[{typst_escape(em)}]')
    if data.get("phone"):
        parts.append(typst_escape(data["phone"]))
    if data.get("linkedin"):
        li = data["linkedin"]
        parts.append(f'#link("https://www.linkedin.com/in/{li}")[linkedin.com/in/{typst_escape(li)}]')
    if data.get("github"):
        gh = data["github"]
        parts.append(f'#link("https://github.com/{gh}")[github.com/{typst_escape(gh)}]')
    if not parts:
        return ""
    joiner = f" #h({gap}) {sep} #h({gap})\n      "
    return joiner.join(parts)


def _contact_sidebar_typst(data: dict[str, Any]) -> str:
    lines: list[str] = []
    if data.get("location"):
        lines.append(typst_escape(data["location"]))
    if data.get("email"):
        em = data["email"]
        lines.append(f'#link("mailto:{em}")[{typst_escape(em)}]')
    if data.get("phone"):
        lines.append(typst_escape(data["phone"]))
    if data.get("linkedin"):
        li = data["linkedin"]
        lines.append(f'#link("https://www.linkedin.com/in/{li}")[linkedin.com/in/{typst_escape(li)}]')
    if data.get("github"):
        gh = data["github"]
        lines.append(f'#link("https://github.com/{gh}")[github.com/{typst_escape(gh)}]')
    return " \\\n    #v(1.5pt)\n    ".join(lines)


def markdown_cv_to_typst_data(md_text: str, frontmatter: dict[str, Any]) -> dict[str, Any]:
    """Convierte un CV Markdown estándar del repo en el diccionario que usa el motor Typst 1-página."""
    idioma = "es" if str(frontmatter.get("idioma") or "en").lower().startswith("es") else "en"
    angulo = str(frontmatter.get("angulo") or "finops").strip().lower()
    if angulo not in THEME_PALETTES:
        angulo = "finops"

    lines = [l.rstrip() for l in md_text.splitlines()]
    name = "Candidate Name"
    headline = ""
    location = ""
    email = ""
    phone = ""
    linkedin = ""
    github = ""

    idx = 0
    header_lines: list[str] = []
    while idx < len(lines) and not lines[idx].strip().startswith("## "):
        s = lines[idx].strip()
        if s.startswith("# "):
            raw_name = _strip_md_stars(s[2:])
            name = raw_name.title() if raw_name.isupper() else raw_name
        elif s and s != "---":
            header_lines.append(s)
        idx += 1

    if header_lines:
        headline = _strip_md_stars(header_lines[0])
    for hl in header_lines[1:]:
        m_mail = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", hl)
        if m_mail:
            email = m_mail.group(1)
        m_phone = re.search(r"(\+\d[\d\s\-]{7,18}\d)", hl)
        if m_phone:
            phone = m_phone.group(1).strip()
        m_li = re.search(r"linkedin\.com/in/([A-Za-z0-9\-_]+)", hl)
        if m_li:
            linkedin = m_li.group(1)
        m_gh = re.search(r"github\.com/([A-Za-z0-9\-_]+)", hl)
        if m_gh:
            github = m_gh.group(1)
        if not m_mail and not m_li and not m_gh and not location:
            raw_loc = hl.split("|")[0].strip()
            if raw_loc:
                location = _clean_location(raw_loc, idioma)

    # Parsear secciones ##
    sec_lines: dict[str, list[str]] = {}
    current_sec: str | None = None
    for line in lines[idx:]:
        s = line.strip()
        if s.startswith("## "):
            current_sec = _strip_md_stars(s[3:]).lower()
            sec_lines[current_sec] = []
        elif current_sec is not None:
            sec_lines[current_sec].append(s)

    summary = ""
    skill_groups: list[dict[str, Any]] = []
    soft_skills: list[str] = []
    experiences: list[dict[str, Any]] = []
    education: list[dict[str, str]] = []
    certifications: list[dict[str, str]] = []
    languages: list[dict[str, Any]] = []
    project: dict[str, Any] | None = None

    for sec_key, slines in sec_lines.items():
        if any(k in sec_key for k in ("summary", "resumen", "perfil")):
            non_empty = [l for l in slines if l and l != "---"]
            summary = " ".join(non_empty)
        elif any(k in sec_key for k in ("soft skill", "habilidades blandas", "competencias blandas")):
            for l in slines:
                if l.startswith("- ") or l.startswith("* "):
                    soft_skills.extend(_split_skill_tags(l[2:].strip(), max_tags=10))
                elif l and l != "---":
                    soft_skills.extend(_split_skill_tags(l, max_tags=10))
        elif any(k in sec_key for k in ("skill", "competencia", "stack", "habilidad")):
            for l in slines:
                if l.startswith("- ") or l.startswith("* "):
                    b = l[2:].strip()
                    m = re.match(r"^\*\*([^*]+?):\*\*\s*(.*)$", b)
                    if m:
                        grp_lbl = m.group(1).strip()
                        if any(k in grp_lbl.lower() for k in ("soft", "blanda", "interpersonal")):
                            soft_skills.extend(_split_skill_tags(m.group(2), max_tags=10))
                        else:
                            skill_groups.append({"label": grp_lbl, "tags": _split_skill_tags(m.group(2), max_tags=11)})
                    else:
                        skill_groups.append({"label": "Skills", "tags": _split_skill_tags(b, max_tags=11)})
        elif any(k in sec_key for k in ("experience", "experiencia")):
            cur: dict[str, Any] | None = None
            for l in slines:
                if not l:
                    continue
                if l.startswith("### "):
                    if cur:
                        experiences.append(cur)
                    raw_h3 = l[4:].strip()
                    parts = re.split(r"\s+[—|–]\s+|\s+\|\s+", raw_h3, maxsplit=1)
                    company = _strip_md_stars(parts[0])
                    position = _strip_md_stars(parts[1]) if len(parts) > 1 else ""
                    cur = {
                        "company": company,
                        "position": position or company,
                        "location": "100% Remoto" if idioma == "es" else "100% Remote",
                        "date": "",
                        "highlights": [],
                    }
                elif cur is not None and l.startswith("*") and l.endswith("*") and not l.startswith("**"):
                    meta_line = _strip_md_stars(l)
                    mparts = [p.strip() for p in meta_line.split("|")]
                    if len(mparts) >= 2:
                        cur["location"] = mparts[0]
                        cur["date"] = mparts[1]
                    elif mparts:
                        cur["date"] = mparts[0]
                elif cur is not None and (l.startswith("- ") or l.startswith("* ")):
                    cur["highlights"].append(l[2:].strip())
            if cur:
                experiences.append(cur)
        elif any(k in sec_key for k in ("project", "proyecto")):
            for l in slines:
                if l.startswith("### ") or (l.startswith("- ") and project is None):
                    m_url = re.search(r"\((https?://[^)]+)\)", l)
                    clean_proj = _strip_md_stars(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", l.lstrip("#- ").strip()))
                    m_date = re.search(r"\(([^)]*\d{4}[^)]*)\)", clean_proj)
                    p_date = m_date.group(1).strip() if m_date else ""
                    if m_date:
                        clean_proj = clean_proj.replace(m_date.group(0), "").strip(" ·-|")
                    project = {
                        "name": clean_proj,
                        "url": m_url.group(1) if m_url else (f"https://github.com/{github}" if github else ""),
                        "date": p_date,
                        "highlights": [],
                    }
                elif project is not None and l.startswith("- "):
                    project["highlights"].append(l[2:].strip())
        elif any(k in sec_key for k in ("educa", "certif", "idioma", "languag")):
            for l in slines:
                if not (l.startswith("- ") or l.startswith("* ")):
                    continue
                b = l[2:].strip()
                low = b.lower()
                if low.startswith("**certif"):
                    rest = re.sub(r"^\*\*[^*]+:\*\*\s*", "", b)
                    for part in re.split(r"\s*\|\s*|,\s*(?=\*\*)", rest):
                        clean = _strip_md_stars(part).strip(" .")
                        if not clean:
                            continue
                        # Soportar "Nombre — Emisor (Fecha)" o "Nombre (Emisor · Fecha)"
                        m_dash = re.match(r"^(.+?)\s+[—–]\s+(.+)$", clean)
                        if m_dash:
                            cname = m_dash.group(1).strip()
                            tail = m_dash.group(2).strip()
                            m_tail = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", tail)
                            issuer = m_tail.group(1).strip() if m_tail else tail
                            cdate = m_tail.group(2).strip() if m_tail else ""
                        else:
                            m_p = re.match(r"^(.+?)\s*\(([^()]+)\)\s*$", clean)
                            if m_p and m_p.group(2).strip() not in ("FOCP", "AWS", "SAA", "C03"):
                                cname = m_p.group(1).strip()
                                raw_inner = m_p.group(2).strip()
                                if "·" in raw_inner:
                                    issuer, cdate = [x.strip() for x in raw_inner.split("·", 1)]
                                elif "," in raw_inner:
                                    issuer, cdate = [x.strip() for x in raw_inner.split(",", 1)]
                                else:
                                    issuer, cdate = raw_inner, ""
                            else:
                                cname, issuer, cdate = clean, "", ""
                        cdate = re.sub(r"(?i)certificado\s+obtenido\s*[—–\-]\s*", "", cdate).strip()
                        certifications.append({"name": cname, "issuer": issuer, "date": cdate})
                elif low.startswith("**idioma") or low.startswith("**languag"):
                    rest = re.sub(r"^\*\*[^*]+:\*\*\s*", "", b)
                    for part in re.split(r"\s*\|\s*|\s*·\s*|,\s*", rest):
                        clean = _strip_md_stars(part).strip(" .")
                        if not clean:
                            continue
                        m_lg = re.match(r"^([^(—–:]+?)(?:\s*\(([^)]+)\)|\s*[:—–]\s*(.+))?$", clean)
                        lg_name = m_lg.group(1).strip() if m_lg else clean
                        raw_lvl = ((m_lg.group(2) or m_lg.group(3)) or "").strip() if m_lg else ""
                        low_lvl = raw_lvl.lower()
                        if "nativ" in low_lvl:
                            lg_lvl = "Nativo" if idioma == "es" else "Native"
                            pct = 100
                        elif "c2" in low_lvl:
                            lg_lvl = "C2"
                            pct = 98
                        elif "c1" in low_lvl:
                            lg_lvl = "C1"
                            pct = 92
                        elif "b2" in low_lvl:
                            lg_lvl = "B2"
                            pct = 82
                        else:
                            lg_lvl = _safe_trim(raw_lvl.split("—")[0].split("-")[0].strip(), 18) or ""
                            pct = 80
                        languages.append({
                            "name": lg_name,
                            "level": lg_lvl,
                            "pct": pct,
                        })
                else:
                    clean = _strip_md_stars(b)
                    # Soportar "Carrera — Universidad Andrés Bello (UNAB) (2023 – 2026)" sin romper "Inglés–Español"
                    m_ed = re.match(r"^(.+?)\s+[—–]\s+(.+?)(?:\s*\(([^()]*\d{4}[^()]*)\))?\s*$", clean)
                    if m_ed:
                        ed_date = (m_ed.group(3) or "").strip()
                        ed_date = re.sub(r"(?i)titulaci[oó]n:\s*", "", ed_date).strip()
                        if "|" in ed_date:
                            ed_date = ed_date.split("|", 1)[0].strip()
                        education.append({
                            "degree": m_ed.group(1).strip(),
                            "institution": m_ed.group(2).strip(),
                            "date": ed_date,
                        })

    # Omitir empleos antiguos muy secundarios solo si hay >3 entradas para mantener 1 página balanceada
    if len(experiences) > 3:
        experiences = [e for e in experiences if "prosud" not in e["company"].lower()]

    if experiences and len(experiences[0]["highlights"]) > 8:
        experiences[0]["highlights"] = experiences[0]["highlights"][:8]

    # Sin casillas KPI cuadradas en ningún layout (prioriza experiencia real y limpieza visual)
    kpis: list[dict[str, str]] = []

    return {
        "name": name,
        "headline": headline,
        "location": location,
        "email": email,
        "phone": phone,
        "linkedin": linkedin,
        "github": github,
        "lang": idioma,
        "kpis": kpis,
        "summary": summary,
        "skill_groups": skill_groups[:4],
        "soft_skills": list(dict.fromkeys(soft_skills))[:8],
        "experiences": experiences[:3],
        "project": project,
        "education": education[:3],
        "certifications": certifications[:4],
        "languages": languages[:3],
    }


def _build_bento_typst(data: dict[str, Any], pal: dict[str, str]) -> str:
    """Arquitectura Bento KPI Dashboard + Timeline calibrada a 1 sola página (1/1)."""
    lang = "es" if str(data.get("lang") or "en").lower().startswith("es") else "en"
    lbl = I18N_LABELS[lang]
    initials = _initials(data["name"])

    kpis = (data.get("kpis") or [])[:4]
    kpi_grid = ""
    if kpis:
        cols = ", ".join(["1fr"] * len(kpis))
        kpi_cards = [
            f"""box(width: 100%, height: 43pt, fill: white, stroke: (left: 2.3pt + accent, rest: 0.55pt + border), radius: 4.5pt, inset: (x: 6.5pt, y: 4.5pt))[
              #text(font: font-display, size: 11.2pt, weight: "bold", fill: dark-bg)[{typst_escape(k['value'])}] \\
              #v(0.5pt)
              #text(size: 6.6pt, weight: "bold", fill: accent)[{typst_escape(k['label'].upper())}] \\
              #text(size: 6.4pt, fill: muted)[{typst_escape(k['sub'])}]
            ]"""
            for k in kpis
        ]
        kpi_grid = f"#v(6pt)\n#grid(columns: ({cols}), column-gutter: 6pt, {', '.join(kpi_cards)})\n#v(6pt)"
    else:
        kpi_grid = "#v(6pt)"

    exp_blocks = []
    for exp in data.get("experiences") or []:
        bullets = "\n".join(f"#bullet-item[{typst_escape(b)}]" for b in exp["highlights"])
        exp_blocks.append(
            f"""#timeline-entry(
              company: [{typst_escape(exp['company'])}],
              role: [{typst_escape(exp['position'])}],
              date: [{typst_escape(exp.get('date', ''))}],
              location: [{typst_escape(exp.get('location', ''))}],
            )[
              {bullets}
            ]"""
        )

    proj = data.get("project")
    proj_section = ""
    if proj and proj.get("highlights"):
        proj_bullets = "\n".join(f"#bullet-item[{typst_escape(b)}]" for b in proj["highlights"])
        title_node = (
            f"link({typst_str(proj['url'])})[#text(font: font-display, size: 8.5pt, weight: \"bold\", fill: dark-bg)[{typst_escape(proj['name'])}]]"
            if proj.get("url")
            else f"text(font: font-display, size: 8.5pt, weight: \"bold\", fill: dark-bg)[{typst_escape(proj['name'])}]"
        )
        date_node = (
            f"box(fill: accent-soft, radius: 7pt, inset: (x: 5pt, y: 1.5pt))[#text(size: 6.6pt, weight: \"bold\", fill: accent)[{typst_escape(proj['date'])}]]"
            if proj.get("date")
            else "[]"
        )
        proj_section = f"""
    #sec-title[{typst_escape(lbl['project_arch'])}]
    #block(width: 100%, fill: surface, stroke: 0.55pt + border, radius: 5pt, inset: (x: 8pt, y: 6.5pt), breakable: false)[
      #grid(columns: (1fr, auto), align: (left, right), {title_node}, {date_node})
      #v(3pt)
      #block(spacing: 2.2pt)[{proj_bullets}]
    ]"""

    skill_groups = []
    for sg in data.get("skill_groups") or []:
        pills = _render_skill_pills_typst(sg["tags"], style="light")
        skill_groups.append(
            f"""#block(breakable: false, spacing: 3.5pt)[
              #text(font: font-display, size: 6.9pt, weight: "bold", fill: dark-bg)[{typst_escape(sg['label'].upper())}]
              #v(1.5pt)
              #set par(leading: 0.58em)
              {pills}
            ]"""
        )

    cert_section = ""
    if data.get("certifications"):
        cert_cards = []
        for c in data["certifications"]:
            sub = f"{typst_escape(c.get('issuer', ''))}{(' · ' + typst_escape(c['date'])) if c.get('date') else ''}".strip(" ·")
            issuer_txt = f" \\ #text(size: 6.2pt, fill: muted)[{sub}]" if sub else ""
            cert_cards.append(
                f"""block(width: 100%, fill: white, stroke: (left: 1.8pt + accent-border, rest: 0.5pt + border), radius: 3.5pt, inset: (x: 5.5pt, y: 2.5pt), breakable: false)[
                  #text(size: 7.0pt, weight: "bold", fill: dark-bg)[{typst_escape(c['name'])}]{issuer_txt}
                ]"""
            )
        cert_section = f"""
    #v(4pt)
    #block(breakable: false)[
      #text(font: font-display, size: 7.8pt, weight: "bold", fill: dark-bg, tracking: 0.5pt)[{typst_escape(lbl['certifications'].upper())}]
      #v(1.5pt)
      #line(length: 100%, stroke: 0.65pt + accent-border)
      #v(3pt)
      #stack(spacing: 2.5pt, {", ".join(cert_cards)})
    ]"""

    soft_section = ""
    if data.get("soft_skills"):
        soft_pills = _render_skill_pills_typst(data["soft_skills"], style="light")
        soft_section = f"""
    #v(4pt)
    #block(breakable: false)[
      #text(font: font-display, size: 7.8pt, weight: "bold", fill: dark-bg, tracking: 0.5pt)[{typst_escape(lbl['soft_skills'].upper())}]
      #v(1.5pt)
      #line(length: 100%, stroke: 0.65pt + accent-border)
      #v(2.5pt)
      #set par(leading: 0.56em)
      {soft_pills}
    ]"""

    edu_section = ""
    if data.get("education"):
        edu_blocks = [
            f"""#block(breakable: false, spacing: 2.8pt)[
              #text(size: 7.2pt, weight: "bold", fill: dark-bg)[{typst_escape(ed['degree'])}] \\
              #text(size: 6.5pt, fill: muted)[{typst_escape(ed['institution'])}{(' · ' + typst_escape(ed['date'])) if ed.get('date') else ''}]
            ]"""
            for ed in data["education"]
        ]
        edu_section = f"""
    #v(4pt)
    #block(breakable: false)[
      #text(font: font-display, size: 7.8pt, weight: "bold", fill: dark-bg, tracking: 0.5pt)[{typst_escape(lbl['education'].upper())}]
      #v(1.5pt)
      #line(length: 100%, stroke: 0.65pt + accent-border)
      #v(3pt)
      {"".join(edu_blocks)}
    ]"""

    lang_section = ""
    if data.get("languages"):
        lang_blocks = [
            f"""#block(breakable: false, spacing: 2.8pt)[
              #grid(columns: (1fr, auto),
                text(size: 7.2pt, weight: "bold", fill: dark-bg)[{typst_escape(lg['name'])}],
                text(size: 6.8pt, weight: "bold", fill: accent)[{typst_escape(lg['level'])}]
              )
              #v(1pt)
              #box(width: 100%, height: 2.5pt, fill: border, radius: 2pt)[
                #place(left, box(width: {lg.get('pct', 90)}%, height: 2.5pt, fill: accent, radius: 2pt))
              ]
            ]"""
            for lg in data["languages"]
        ]
        lang_section = f"""
    #v(4pt)
    #block(breakable: false)[
      #text(font: font-display, size: 7.8pt, weight: "bold", fill: dark-bg, tracking: 0.5pt)[{typst_escape(lbl['languages'].upper())}]
      #v(1.5pt)
      #line(length: 100%, stroke: 0.65pt + accent-border)
      #v(2.5pt)
      {"".join(lang_blocks)}
    ]"""

    return f"""
#let dark-bg = rgb({typst_str(pal['dark_bg'])})
#let dark-bg-alt = rgb({typst_str(pal['dark_bg_alt'])})
#let accent = rgb({typst_str(pal['accent'])})
#let accent-bright = rgb({typst_str(pal['accent_bright'])})
#let accent-soft = rgb({typst_str(pal['accent_soft'])})
#let accent-border = rgb({typst_str(pal['accent_border'])})
#let text-main = rgb({typst_str(pal['text'])})
#let muted = rgb({typst_str(pal['muted'])})
#let surface = rgb({typst_str(pal['surface'])})
#let border = rgb({typst_str(pal['border'])})
#let font-display = {typst_str(pal['font_display'])}
#let font-body = {typst_str(pal['font_body'])}

#set page(
  paper: "us-letter",
  margin: (top: 0.34in, bottom: 0.3in, left: 0.4in, right: 0.4in),
)

#set text(font: font-body, size: 8.1pt, fill: text-main)
#set par(leading: 0.48em, justify: false)

#let sec-title(title) = {{
  v(5.5pt)
  grid(columns: (auto, 1fr), column-gutter: 6pt, align: horizon,
    text(font: font-display, size: 8.9pt, weight: "bold", fill: dark-bg, tracking: 0.5pt)[#upper(title)],
    line(length: 100%, stroke: 0.8pt + accent-border)
  )
  v(4pt)
}}

#let pill-light(body) = box(
  fill: white,
  stroke: 0.5pt + border,
  radius: 3pt,
  inset: (x: 4pt, y: 1.8pt),
  baseline: 18%,
)[#text(size: 6.7pt, weight: "medium", fill: dark-bg)[#body]]

#let bullet-item(body) = grid(
  columns: (7pt, 1fr),
  column-gutter: 2.5pt,
  text(fill: accent, weight: "bold", size: 7.8pt)[▸],
  par(leading: 0.46em)[#text(size: 7.75pt)[#body]]
)

#let timeline-entry(company: [], role: [], date: [], location: [], body) = {{
  block(breakable: false, spacing: 5.5pt)[
    #grid(
      columns: (8pt, 1fr),
      column-gutter: 5pt,
      [
        #v(2.5pt)
        #circle(radius: 2.6pt, fill: accent, stroke: 1.2pt + accent-soft)
      ],
      [
        #grid(columns: (1fr, auto), align: (left, right), column-gutter: 5pt,
          [
            #text(font: font-display, size: 8.8pt, weight: "bold", fill: dark-bg)[#role]
            #h(4pt) #text(size: 7.7pt, weight: "bold", fill: accent)[· #company]
            #h(3pt) #text(size: 7.1pt, fill: muted)[(#location)]
          ],
          box(fill: accent-soft, stroke: 0.45pt + accent-border, radius: 8pt, inset: (x: 5.5pt, y: 1.8pt))[
            #text(size: 6.7pt, weight: "bold", fill: dark-bg)[#date]
          ]
        )
        #v(2.5pt)
        #block(spacing: 2.5pt)[#body]
      ]
    )
  ]
}}

#pad(x: -0.4in, top: -0.34in, bottom: 0pt)[
  #block(width: 100%, fill: dark-bg, inset: (x: 0.4in, top: 13pt, bottom: 11pt))[
    #grid(columns: (1fr, auto), align: (left, top),
      [
        #box(fill: dark-bg-alt, stroke: 0.55pt + accent-bright, radius: 9pt, inset: (x: 7pt, y: 2pt))[
          #text(font: font-display, size: 6.4pt, weight: "bold", fill: accent-bright, tracking: 0.7pt)[{typst_escape(pal['badge_label'])}]
        ]
        #v(3.5pt)
        #text(font: font-display, size: 19pt, weight: "bold", fill: white, tracking: 0.2pt)[{typst_escape(data['name'])}]
        #v(1.5pt)
        #text(size: 8.6pt, fill: rgb("#D1D5DB"))[{typst_escape(data['headline'])}]
      ],
      box(width: 32pt, height: 32pt, fill: accent, radius: 7pt)[
        #align(center + horizon)[#text(font: font-display, size: 13pt, weight: "bold", fill: white)[{typst_escape(initials)}]]
      ]
    )
    #v(6pt)
    #line(length: 100%, stroke: 0.45pt + rgb("#334155"))
    #v(4pt)
    #text(size: 7.0pt, fill: rgb("#E2E8F0"))[
      {_contact_line_typst(data, sep="·", gap="5pt")}
    ]
  ]
]

{kpi_grid}

#grid(
  columns: (65.5%, 32%),
  column-gutter: 2.5%,
  [
    #block(width: 100%, fill: surface, stroke: (left: 2.3pt + dark-bg, rest: 0.5pt + border), radius: 4.5pt, inset: (x: 8pt, y: 6.5pt))[
      #text(font: font-display, size: 6.9pt, weight: "bold", fill: accent, tracking: 0.5pt)[{typst_escape(lbl['executive_profile'].upper())}]
      #v(2pt)
      #par(leading: 0.46em)[#text(size: 7.8pt, fill: text-main)[{typst_escape(data['summary'])}]]
    ]

    #sec-title[{typst_escape(lbl['experience'])}]
    {"".join(exp_blocks)}
    {proj_section}
  ],
  [
    #block(width: 100%, fill: surface, stroke: 0.55pt + border, radius: 5pt, inset: (x: 7.5pt, y: 6.5pt))[
      #text(font: font-display, size: 8.2pt, weight: "bold", fill: dark-bg, tracking: 0.5pt)[{typst_escape(lbl['core_tech_stack'].upper())}]
      #v(3pt)
      #line(length: 100%, stroke: 0.65pt + accent-border)
      #v(4pt)
      {"".join(skill_groups)}
    ]
    {soft_section}
    {cert_section}
    {edu_section}
    {lang_section}
  ]
)
"""


def _build_editorial_typst(data: dict[str, Any], pal: dict[str, str]) -> str:
    """Arquitectura Swiss Editorial Split Sidebar calibrada a 1 sola página (1/1)."""
    lang = "es" if str(data.get("lang") or "en").lower().startswith("es") else "en"
    lbl = I18N_LABELS[lang]
    initials = _initials(data["name"])
    stacked_name = " \\\n".join(typst_escape(p) for p in data["name"].split())

    kpis = (data.get("kpis") or [])[:3]
    kpi_block = ""
    if kpis:
        cols = ", ".join(["1fr"] * len(kpis))
        kpi_strip = [
            f"""[
              #text(font: font-display, size: 12.8pt, weight: "bold", fill: accent)[{typst_escape(k['value'])}] \\
              #text(size: 7pt, weight: "bold", fill: dark-bg)[{typst_escape(k['label'])}] \\
              #text(size: 6.6pt, fill: muted)[{typst_escape(k['sub'])}]
            ]"""
            for k in kpis
        ]
        kpi_block = f"""
    #v(6pt)
    #block(width: 100%, stroke: (top: 0.55pt + border, bottom: 0.55pt + border), inset: (y: 5.5pt))[
      #grid(columns: ({cols}), column-gutter: 7pt, {", ".join(kpi_strip)})
    ]"""

    exp_blocks = []
    for exp in data.get("experiences") or []:
        bullets = "\n".join(f"#bullet-item[{typst_escape(b)}]" for b in exp["highlights"])
        exp_blocks.append(
            f"""#block(width: 100%, stroke: (left: 1.8pt + accent-border), inset: (left: 8pt, y: 0.5pt), spacing: 5.5pt, breakable: false)[
              #grid(columns: (1fr, auto), align: (left, right), column-gutter: 5pt,
                [
                  #text(font: font-display, size: 8.9pt, weight: "bold", fill: dark-bg)[{typst_escape(exp['position'])}]
                  #h(4pt) #text(size: 7.7pt, weight: "bold", fill: accent)[· {typst_escape(exp['company'])}]
                  #h(3pt) #text(size: 7.1pt, fill: muted)[({typst_escape(exp.get('location', ''))})]
                ],
                box(fill: surface, stroke: 0.45pt + border, radius: 3.5pt, inset: (x: 5pt, y: 1.8pt))[
                  #text(size: 6.7pt, weight: "bold", fill: dark-bg)[{typst_escape(exp.get('date', ''))}]
                ]
              )
              #v(2.5pt)
              #block(spacing: 2.3pt)[{bullets}]
            ]"""
        )

    proj = data.get("project")
    proj_section = ""
    if proj and proj.get("highlights"):
        proj_bullets = "\n".join(f"#bullet-item[{typst_escape(b)}]" for b in proj["highlights"])
        title_node = (
            f"link({typst_str(proj['url'])})[#text(font: font-display, size: 8.5pt, weight: \"bold\", fill: dark-bg)[{typst_escape(proj['name'])}]]"
            if proj.get("url")
            else f"text(font: font-display, size: 8.5pt, weight: \"bold\", fill: dark-bg)[{typst_escape(proj['name'])}]"
        )
        proj_section = f"""
    #right-sec[{typst_escape(lbl['project'])}]
    #block(width: 100%, fill: surface, stroke: 0.55pt + border, radius: 5pt, inset: (x: 8pt, y: 6pt), breakable: false)[
      #grid(columns: (1fr, auto), align: (left, right),
        {title_node},
        text(size: 7pt, weight: "bold", fill: accent)[{typst_escape(proj.get('date', ''))}]
      )
      #v(2.5pt)
      #block(spacing: 2pt)[{proj_bullets}]
    ]"""

    skill_groups = []
    for sg in data.get("skill_groups") or []:
        pills = _render_skill_pills_typst(sg["tags"], style="dark")
        skill_groups.append(
            f"""#block(breakable: false, spacing: 5.5pt)[
              #text(font: font-display, size: 6.7pt, weight: "bold", fill: accent-bright, tracking: 0.45pt)[{typst_escape(sg['label'].upper())}]
              #v(2pt)
              #set par(leading: 0.66em)
              {pills}
            ]"""
        )

    cert_items = [
        f"""#block(breakable: false, spacing: 3.2pt)[
          #text(size: 7.2pt, weight: "bold", fill: white)[{typst_escape(c['name'])}] \\
          #text(size: 6.4pt, fill: rgb("#CBD5E1"))[{typst_escape(c.get('issuer', ''))}{(' · ' + typst_escape(c['date'])) if c.get('date') else ''}]
        ]"""
        for c in (data.get("certifications") or [])
    ]

    soft_sidebar = ""
    if data.get("soft_skills"):
        soft_pills = _render_skill_pills_typst(data["soft_skills"], style="dark")
        soft_sidebar = f"""
    #left-sec[{typst_escape(lbl['soft_skills'])}]
    #set par(leading: 0.64em)
    {soft_pills}"""

    edu_section = ""
    if data.get("education"):
        edu_cols = ", ".join(["1fr"] * len(data["education"]))
        edu_right_items = [
            f"""block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
              #text(size: 7.2pt, weight: "bold", fill: dark-bg)[{typst_escape(ed['degree'])}] \\
              #text(size: 6.5pt, fill: text-main)[{typst_escape(ed['institution'])}] \\
              #text(size: 6.4pt, weight: "bold", fill: accent)[{typst_escape(ed.get('date', ''))}]
            ]"""
            for ed in data["education"]
        ]
        edu_section = f"""
    #right-sec[{typst_escape(lbl['education'])}]
    #grid(columns: ({edu_cols}), column-gutter: 5pt, {", ".join(edu_right_items)})"""

    lang_items = [
        f"""#block(breakable: false, spacing: 2.5pt)[
          #text(size: 7.3pt, weight: "bold", fill: white)[{typst_escape(lg['name'])}] #text(size: 7.0pt, fill: accent-bright)[· {typst_escape(lg['level'])}]
        ]"""
        for lg in data.get("languages") or []
    ]

    return f"""
#let dark-bg = rgb({typst_str(pal['dark_bg'])})
#let dark-bg-alt = rgb({typst_str(pal['dark_bg_alt'])})
#let accent = rgb({typst_str(pal['accent'])})
#let accent-bright = rgb({typst_str(pal['accent_bright'])})
#let accent-soft = rgb({typst_str(pal['accent_soft'])})
#let accent-border = rgb({typst_str(pal['accent_border'])})
#let text-main = rgb({typst_str(pal['text'])})
#let muted = rgb({typst_str(pal['muted'])})
#let surface = rgb({typst_str(pal['surface'])})
#let border = rgb({typst_str(pal['border'])})
#let font-display = {typst_str(pal['font_display'])}
#let font-body = {typst_str(pal['font_body'])}

#set page(
  paper: "us-letter",
  margin: (top: 0.34in, bottom: 0.3in, left: 0.28in, right: 0.38in),
  background: place(left + top, rect(width: 2.55in, height: 100%, fill: dark-bg)),
)

#set text(font: font-body, size: 8.1pt, fill: text-main)
#set par(leading: 0.48em)

#let pill-dark(body) = box(
  fill: dark-bg-alt,
  stroke: 0.45pt + rgb("#334155"),
  radius: 3pt,
  inset: (x: 4pt, y: 1.7pt),
  baseline: 18%,
)[#text(size: 6.5pt, fill: rgb("#F1F5F9"))[#body]]

#let bullet-item(body) = grid(
  columns: (7pt, 1fr),
  column-gutter: 2.5pt,
  text(fill: accent, weight: "bold", size: 7.8pt)[—],
  par(leading: 0.46em)[#text(size: 7.75pt)[#body]]
)

#let right-sec(title) = {{
  v(6pt)
  text(font: font-display, size: 8.8pt, weight: "bold", fill: dark-bg, tracking: 0.9pt)[#upper(title)]
  v(1.5pt)
  line(length: 30pt, stroke: 1.8pt + accent)
  v(4pt)
}}

#let left-sec(title) = {{
  v(6.5pt)
  text(font: font-display, size: 7.3pt, weight: "bold", fill: accent-bright, tracking: 0.9pt)[#upper(title)]
  v(1.5pt)
  line(length: 100%, stroke: 0.45pt + rgb("#334155"))
  v(3.5pt)
}}

#grid(
  columns: (2.06in, 1fr),
  column-gutter: 0.38in,
  [
    #box(width: 30pt, height: 30pt, fill: accent, radius: 6pt)[
      #align(center + horizon)[#text(font: font-display, size: 12.5pt, weight: "bold", fill: white)[{typst_escape(initials)}]]
    ]
    #v(5pt)
    #text(font: font-display, size: 16.5pt, weight: "bold", fill: white)[{stacked_name}]
    #v(3pt)
    #line(length: 28pt, stroke: 1.8pt + accent-bright)
    #v(4pt)
    #text(size: 6.9pt, weight: "bold", fill: accent-bright, tracking: 0.45pt)[{typst_escape(pal['badge_label'])}]

    #left-sec[{typst_escape(lbl['contact'])}]
    #set text(size: 7.1pt, fill: rgb("#E2E8F0"))
    {_contact_sidebar_typst(data)}

    #left-sec[{typst_escape(lbl['tech_stack'])}]
    {"".join(skill_groups)}
    {soft_sidebar}

    #left-sec[{typst_escape(lbl['certifications'])}]
    {"".join(cert_items)}

    #left-sec[{typst_escape(lbl['languages'])}]
    {"".join(lang_items)}
  ],
  [
    #text(font: font-display, size: 10.6pt, weight: "bold", fill: dark-bg)[{typst_escape(data['headline'])}]
    #v(4.5pt)
    #block(width: 100%, fill: accent-soft, stroke: (left: 2.8pt + accent), radius: (right: 4.5pt), inset: (x: 8.5pt, y: 6.5pt))[
      #par(leading: 0.47em)[#text(size: 7.9pt, fill: text-main)[{typst_escape(data['summary'])}]]
    ]
    {kpi_block}

    #right-sec[{typst_escape(lbl['experience'])}]
    {"".join(exp_blocks)}
    {proj_section}
    {edu_section}
  ]
)
"""


def _build_tech_banner_typst(data: dict[str, Any], pal: dict[str, str]) -> str:
    """Arquitectura Linear Tech Banner + 2×2 Skill Grid calibrada a 1 sola página (1/1)."""
    lang = "es" if str(data.get("lang") or "en").lower().startswith("es") else "en"
    lbl = I18N_LABELS[lang]

    kpis = (data.get("kpis") or [])[:4]
    kpi_grid = ""
    if kpis:
        cols = ", ".join(["1fr"] * len(kpis))
        kpi_cells = [
            f"""block(width: 100%, height: 37pt, fill: surface, stroke: (top: 2pt + accent, rest: 0.5pt + border), radius: 4pt, inset: (x: 6pt, y: 4pt))[
              #text(font: font-display, size: 10.4pt, weight: "bold", fill: dark-bg)[{typst_escape(k['value'])}] \\
              #text(size: 6.2pt, weight: "bold", fill: accent)[{typst_escape(k['label'].upper())}] \\
              #text(size: 6.0pt, fill: muted)[{typst_escape(k['sub'])}]
            ]"""
            for k in kpis
        ]
        kpi_grid = f"#v(4.5pt)\n#grid(columns: ({cols}), column-gutter: 5.5pt, {', '.join(kpi_cells)})"

    skill_cards = []
    for sg in (data.get("skill_groups") or [])[:4]:
        pills = _render_skill_pills_typst(sg["tags"], style="light")
        skill_cards.append(
            f"""block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4pt), breakable: false)[
              #text(font: font-display, size: 6.6pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(sg['label'].upper())}]
              #v(1.5pt)
              #set par(leading: 0.56em)
              {pills}
            ]"""
        )

    exp_cards = []
    for exp in data.get("experiences") or []:
        bullets = "\n".join(f"#bullet-item[{typst_escape(b)}]" for b in exp["highlights"])
        exp_cards.append(
            f"""#block(width: 100%, stroke: (left: 2pt + accent, rest: 0.5pt + border), radius: 4pt, inset: (x: 7.5pt, y: 4.5pt), spacing: 3.5pt, breakable: false)[
              #grid(columns: (1fr, auto), align: (left, right), column-gutter: 5pt,
                [
                  #text(font: font-display, size: 8.3pt, weight: "bold", fill: dark-bg)[{typst_escape(exp['position'])}]
                  #h(3.5pt) #text(size: 7.3pt, weight: "bold", fill: accent)[· {typst_escape(exp['company'])}]
                  #h(3.5pt) #text(size: 6.8pt, fill: muted)[({typst_escape(exp.get('location', ''))})]
                ],
                box(fill: accent-soft, stroke: 0.45pt + accent-border, radius: 7pt, inset: (x: 5.5pt, y: 1.2pt))[
                  #text(size: 6.4pt, weight: "bold", fill: dark-bg)[{typst_escape(exp.get('date', ''))}]
                ]
              )
              #v(2pt)
              #block(spacing: 1.8pt)[{bullets}]
            ]"""
        )

    edu_lines = "\n".join(
        f"""#block(spacing: 2.2pt)[
          #text(size: 6.9pt, weight: "bold", fill: dark-bg)[{typst_escape(ed['degree'])}] \\
          #text(size: 6.3pt, fill: muted)[{typst_escape(ed['institution'])}{(' · ' + typst_escape(ed['date'])) if ed.get('date') else ''}]
        ]"""
        for ed in data.get("education") or []
    )

    cert_lines = "\n".join(
        f"""#block(spacing: 1.8pt)[
          #text(size: 6.8pt, weight: "bold", fill: dark-bg)[{typst_escape(c['name'])}]
          #text(size: 6.2pt, fill: muted)[{(' · ' + typst_escape(c['issuer'])) if c.get('issuer') else ''}{(' (' + typst_escape(c['date']) + ')') if c.get('date') else ''}]
        ]"""
        for c in (data.get("certifications") or [])
    )

    lang_line = " · ".join(
        f"#strong[{typst_escape(lg['name'])} ({typst_escape(lg['level'])})]"
        for lg in data.get("languages") or []
    )

    soft_pills_tb = _render_skill_pills_typst(data.get("soft_skills") or [], style="light")
    proj = data.get("project")
    if proj and proj.get("highlights"):
        proj_bullets = "\n".join(f"#bullet-item[{typst_escape(b)}]" for b in proj["highlights"])
        p_title = (
            f"#link({typst_str(proj['url'])})[#text(font: font-display, size: 7.4pt, weight: \"bold\", fill: dark-bg)[{typst_escape(proj['name'])}]]"
            if proj.get("url")
            else f"#text(font: font-display, size: 7.4pt, weight: \"bold\", fill: dark-bg)[{typst_escape(proj['name'])}]"
        )
        p_tag = f"{typst_escape(lbl['open_source'])}{(' · ' + typst_escape(proj['date'].upper())) if proj.get('date') else ''}"
        bottom_grid = f"""#grid(columns: (32%, 31%, 34%), column-gutter: 1.5%,
  block(width: 100%, fill: accent-soft, stroke: 0.5pt + accent-border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{p_tag}] \\
    #v(1pt)
    {p_title}
    #v(2pt)
    #block(spacing: 1.5pt)[{proj_bullets}]
  ],
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['education'].upper())}]
    #v(2pt)
    {edu_lines}
  ],
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['certifications_languages'].upper())}]
    #v(2pt)
    {cert_lines}
    #v(2.5pt)
    #line(length: 100%, stroke: 0.4pt + border)
    #v(2pt)
    #text(size: 6.7pt)[{lang_line}]
  ]
)"""
    elif soft_pills_tb:
        bottom_grid = f"""#grid(columns: (32%, 36%, 29%), column-gutter: 1.5%,
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['education'].upper())}]
    #v(2pt)
    {edu_lines}
  ],
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['certifications_languages'].upper())}]
    #v(2pt)
    {cert_lines}
    #v(2.5pt)
    #line(length: 100%, stroke: 0.4pt + border)
    #v(2pt)
    #text(size: 6.7pt)[{lang_line}]
  ],
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['soft_skills'].upper())}]
    #v(2pt)
    #set par(leading: 0.56em)
    {soft_pills_tb}
  ]
)"""
    else:
        bottom_grid = f"""#grid(columns: (48%, 50%), column-gutter: 2%,
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['education'].upper())}]
    #v(2pt)
    {edu_lines}
  ],
  block(width: 100%, fill: surface, stroke: 0.5pt + border, radius: 4pt, inset: (x: 6.5pt, y: 4.5pt), breakable: false)[
    #text(font: font-display, size: 6.5pt, weight: "bold", fill: accent, tracking: 0.4pt)[{typst_escape(lbl['certifications_languages'].upper())}]
    #v(2pt)
    {cert_lines}
    #v(2.5pt)
    #line(length: 100%, stroke: 0.4pt + border)
    #v(2pt)
    #text(size: 6.7pt)[{lang_line}]
  ]
)"""

    return f"""
#let dark-bg = rgb({typst_str(pal['dark_bg'])})
#let dark-bg-alt = rgb({typst_str(pal['dark_bg_alt'])})
#let accent = rgb({typst_str(pal['accent'])})
#let accent-bright = rgb({typst_str(pal['accent_bright'])})
#let accent-soft = rgb({typst_str(pal['accent_soft'])})
#let accent-border = rgb({typst_str(pal['accent_border'])})
#let text-main = rgb({typst_str(pal['text'])})
#let muted = rgb({typst_str(pal['muted'])})
#let surface = rgb({typst_str(pal['surface'])})
#let border = rgb({typst_str(pal['border'])})
#let font-display = {typst_str(pal['font_display'])}
#let font-body = {typst_str(pal['font_body'])}

#set page(
  paper: "us-letter",
  margin: (top: 0.28in, bottom: 0.26in, left: 0.38in, right: 0.38in),
)

#set text(font: font-body, size: 7.7pt, fill: text-main)
#set par(leading: 0.44em)

#let pill-light(body) = box(
  fill: white,
  stroke: 0.45pt + border,
  radius: 2.8pt,
  inset: (x: 3.8pt, y: 1.4pt),
  baseline: 18%,
)[#text(size: 6.35pt, weight: "medium", fill: dark-bg)[#body]]

#let bullet-item(body) = grid(
  columns: (6.5pt, 1fr),
  column-gutter: 2pt,
  text(fill: accent, weight: "bold", size: 7.3pt)[▸],
  par(leading: 0.42em)[#text(size: 7.25pt)[#body]]
)

#let sec-band(num, title) = {{
  v(4pt)
  grid(columns: (auto, auto, 1fr), column-gutter: 5pt, align: horizon,
    box(fill: dark-bg, radius: 2.5pt, inset: (x: 4pt, y: 1.2pt))[
      #text(font: font-display, size: 6.2pt, weight: "bold", fill: accent-bright)[#num]
    ],
    text(font: font-display, size: 8.1pt, weight: "bold", fill: dark-bg, tracking: 0.65pt)[#upper(title)],
    line(length: 100%, stroke: 0.55pt + border)
  )
  v(2.5pt)
}}

#block(width: 100%, fill: dark-bg, stroke: (left: 4.5pt + accent-bright), radius: 5.5pt, inset: (x: 12pt, y: 7.5pt))[
  #grid(columns: (1fr, auto), align: (left, horizon),
    [
      #text(font: font-display, size: 17pt, weight: "bold", fill: white)[{typst_escape(data['name'])}]
      #v(1pt)
      #text(size: 7.9pt, weight: "medium", fill: accent-bright)[{typst_escape(data['headline'])}]
    ],
    box(fill: dark-bg-alt, stroke: 0.5pt + accent-bright, radius: 9pt, inset: (x: 7pt, y: 2.5pt))[
      #text(font: font-display, size: 6pt, weight: "bold", fill: white, tracking: 0.6pt)[{typst_escape(pal['badge_label'])}]
    ]
  )
  #v(4.5pt)
  #line(length: 100%, stroke: 0.4pt + rgb("#334155"))
  #v(3.5pt)
  #text(size: 6.65pt, fill: rgb("#E2E8F0"))[
    {_contact_line_typst(data, sep="|", gap="3.5pt")}
  ]
]

{kpi_grid}

#sec-band("01", {typst_str(lbl['executive_summary'])})
#par(leading: 0.44em)[#text(size: 7.45pt)[{typst_escape(data['summary'])}]]

#sec-band("02", {typst_str(lbl['core_technical_stack'])})
#grid(columns: (1fr, 1fr), column-gutter: 5.5pt, row-gutter: 4pt, {", ".join(skill_cards)})

#sec-band("03", {typst_str(lbl['experience'])})
{"".join(exp_cards)}

#sec-band("04", {typst_str(lbl['bottom_sec'])})
{bottom_grid}
"""


def compile_cv(
    data: dict[str, Any],
    angulo: str,
    layout: str | None,
    pdf_path: Path,
    png_prefix: Path | None = None,
    keep_typ: bool = False,
) -> tuple[bool, list[Path], str]:
    """Compila un CV vanguardista a PDF y PNGs usando Typst embebido."""
    ang = (angulo or "finops").strip().lower()
    chosen_layout = layout or DEFAULT_LAYOUT_BY_ANGULO.get(ang, "editorial")
    pal = THEME_PALETTES.get(ang, THEME_PALETTES["finops"])

    if chosen_layout == "bento":
        src = _build_bento_typst(data, pal)
    elif chosen_layout == "editorial":
        src = _build_editorial_typst(data, pal)
    elif chosen_layout == "tech-banner":
        src = _build_tech_banner_typst(data, pal)
    else:
        return False, [], f"Layout desconocido: {chosen_layout}"

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    typ_path = pdf_path.with_suffix(".typ")
    typ_path.write_text(src, encoding="utf-8")

    try:
        pdf_bytes = typst.compile(str(typ_path), font_paths=FONT_PATHS, format="pdf")
        pdf_path.write_bytes(pdf_bytes)

        png_paths: list[Path] = []
        if png_prefix is not None:
            for old in png_prefix.parent.glob(f"{png_prefix.stem}_*.png"):
                old.unlink()
            pages = typst.compile(str(typ_path), font_paths=FONT_PATHS, format="png", ppi=150.0)
            if isinstance(pages, bytes):
                pages = [pages]
            for i, img_bytes in enumerate(pages, start=1):
                out_png = png_prefix.parent / f"{png_prefix.stem}_{i}.png"
                out_png.write_bytes(img_bytes)
                png_paths.append(out_png)
    except Exception as exc:
        return False, [], str(exc)
    finally:
        if not keep_typ and typ_path.is_file():
            try:
                typ_path.unlink()
            except OSError:
                pass

    return True, png_paths, "OK"


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Compila un CV Markdown o JSON a PDF Typst 1-página.")
    parser.add_argument("--md", help="Ruta al CV Markdown con frontmatter YAML")
    parser.add_argument("--json", dest="json_file", help="Ruta a payload JSON estructurado")
    parser.add_argument("--pdf", required=True, help="Ruta del PDF de salida")
    parser.add_argument("--angulo", default=None, help="Ángulo (finops|ai-automation|devsecops|secops-siem|base)")
    parser.add_argument("--layout", default=None, choices=["bento", "editorial", "tech-banner"])
    parser.add_argument("--png-prefix", default=None, help="Prefijo opcional para generar PNG de vista previa")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    png_prefix = Path(args.png_prefix).resolve() if args.png_prefix else None

    if args.md:
        import yaml

        raw = Path(args.md).read_text(encoding="utf-8")
        fm: dict[str, Any] = {}
        body = raw
        if raw.startswith("---\n"):
            end = raw.find("\n---\n", 4)
            if end != -1:
                fm = yaml.safe_load(raw[4:end]) or {}
                body = raw[end + 5 :].lstrip("\n")
        angulo = args.angulo or str(fm.get("angulo") or "finops")
        data = markdown_cv_to_typst_data(body, fm)
    elif args.json_file:
        data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        angulo = args.angulo or "finops"
    else:
        print("Error: indica --md o --json", file=sys.stderr)
        return 1

    ok, _, msg = compile_cv(data=data, angulo=angulo, layout=args.layout, pdf_path=pdf_path, png_prefix=png_prefix)
    if not ok:
        print(msg, file=sys.stderr)
        return 1
    chosen = args.layout or DEFAULT_LAYOUT_BY_ANGULO.get(angulo.lower(), "editorial")
    print(f"OK:{angulo}:{chosen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
