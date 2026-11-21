#!/usr/bin/env python3
"""Convierte un CV en Markdown (cvs-generados/.../*.md) a HTML ATS y PDF.

Motores soportados para PDF (`--engine`):
- `auto` (por defecto): usa el motor vanguardista **Typst 1-página** (`typst_cv_engine.py`)
  con los diseños oficiales por ángulo elegidos por Felipe:
    - `finops`        -> `editorial`   (Esmeralda + Grafito)
    - `ai-automation` -> `bento`       (Índigo + Cian)
    - `devsecops`     -> `editorial`   (Navy Obsidiana + Ámbar AWS)
    - `secops-siem`   -> `tech-banner` (Grafito + Carmesí)
  Si Typst no está disponible, hace fallback a `rendercv` o a Chrome headless (`html`).
- `typst`    : fuerza el motor vanguardista 1-página en Typst.
- `rendercv` : usa los temas clásicos de RenderCV.
- `html`     : usa el renderizador clásico HTML -> Chrome headless.

El archivo `.html` siempre se genera para mantener compatibilidad con `estado.py validar`
y vistas previas en navegador.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[3]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


ATS_CSS = """
@page {
    size: A4;
    margin: 14mm 16mm 14mm 16mm;
}
* {
    box-sizing: border-box;
}
body {
    font-family: 'Calibri', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.38;
    color: #111827;
    margin: 0;
    padding: 24px 36px;
    max-width: 840px;
    margin-left: auto;
    margin-right: auto;
    background: #ffffff;
}
.no-print-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #0f172a;
    color: #f8fafc;
    padding: 10px 16px;
    border-radius: 8px;
    margin-bottom: 20px;
    font-size: 9.5pt;
}
.no-print-bar button {
    background: #2563eb;
    color: #ffffff;
    border: none;
    padding: 7px 14px;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
}
.no-print-bar button:hover {
    background: #1d4ed8;
}
@media print {
    body {
        padding: 0;
        max-width: none;
    }
    .no-print-bar {
        display: none !important;
    }
}
h1 {
    font-size: 20pt;
    font-weight: 700;
    letter-spacing: 0.5px;
    margin: 0 0 2px 0;
    color: #0f172a;
    text-transform: uppercase;
}
h2 {
    font-size: 11pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #0f172a;
    border-bottom: 1.5px solid #1e293b;
    padding-bottom: 2px;
    margin: 12px 0 6px 0;
}
h3 {
    font-size: 10.8pt;
    font-weight: 700;
    margin: 8px 0 2px 0;
    color: #111827;
}
p {
    margin: 3px 0 6px 0;
}
ul {
    margin: 4px 0 8px 0;
    padding-left: 18px;
}
li {
    margin-bottom: 3.5px;
}
hr {
    border: none;
    border-top: 1px solid #cbd5e1;
    margin: 8px 0;
}
strong {
    font-weight: 700;
}
em {
    font-style: italic;
    color: #334155;
}
a {
    color: #0f172a;
    text-decoration: none;
}
"""


def inline_format(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    return text


def split_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Separa el frontmatter YAML inicial del cuerpo Markdown."""
    if not md_text.startswith("---\n"):
        return {}, md_text
    end = md_text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("Frontmatter YAML sin cierre '---'")
    meta = yaml.safe_load(md_text[4:end]) or {}
    body = md_text[end + 5 :].lstrip("\n")
    return meta if isinstance(meta, dict) else {}, body


def strip_frontmatter(md_text: str) -> str:
    _, body = split_frontmatter(md_text)
    return body


def markdown_to_html(md_text: str) -> str:
    lines = md_text.splitlines()
    html_lines = []
    in_list = False
    paragraph_buffer = []

    def flush_paragraph():
        nonlocal paragraph_buffer
        if paragraph_buffer:
            joined = "<br>\n".join(inline_format(line) for line in paragraph_buffer)
            html_lines.append(f"<p>{joined}</p>")
            paragraph_buffer = []

    def close_list():
        nonlocal in_list
        if in_list:
            html_lines.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue

        if line.startswith("---"):
            flush_paragraph()
            close_list()
            html_lines.append("<hr>")
        elif line.startswith("# "):
            flush_paragraph()
            close_list()
            html_lines.append(f"<h1>{inline_format(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            flush_paragraph()
            close_list()
            html_lines.append(f"<h2>{inline_format(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            flush_paragraph()
            close_list()
            html_lines.append(f"<h3>{inline_format(line[4:].strip())}</h3>")
        elif line.startswith("- ") or line.startswith("* "):
            flush_paragraph()
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{inline_format(line[2:].strip())}</li>")
        else:
            close_list()
            paragraph_buffer.append(line)

    flush_paragraph()
    close_list()

    body = "\n".join(html_lines)
    m_h1 = re.search(r"^#\s+(.+)$", md_text, flags=re.MULTILINE)
    doc_title = f"CV - {m_h1.group(1).strip()}" if m_h1 else "CV ATS"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{doc_title}</title>
<style>{ATS_CSS}</style>
</head>
<body>
<div class="no-print-bar">
  <span><strong>Vista previa CV ATS</strong> — Al imprimir, desmarca "Encabezados y pies de página"</span>
  <button onclick="window.print()">📄 Guardar como PDF</button>
</div>
{body}
</body>
</html>
"""


def _find_typst_python() -> str | None:
    """Devuelve un intérprete Python con `typst` + `rendercv_fonts` instalado."""
    try:
        import rendercv_fonts  # noqa: F401
        import typst  # noqa: F401

        return sys.executable
    except ImportError:
        pass
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return None


def render_with_typst_engine(
    md_path: Path,
    pdf_path: Path,
    angulo: str | None = None,
    layout: str | None = None,
) -> tuple[bool, str]:
    """Ejecuta `typst_cv_engine.py` usando Python actual o `.venv/bin/python`."""
    py_bin = _find_typst_python()
    engine_script = SCRIPT_DIR / "typst_cv_engine.py"
    if not py_bin or not engine_script.is_file():
        return False, "Typst no disponible en el entorno actual ni en .venv"

    cmd = [py_bin, str(engine_script), "--md", str(md_path), "--pdf", str(pdf_path)]
    if angulo:
        cmd.extend(["--angulo", angulo])
    if layout:
        cmd.extend(["--layout", layout])

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and pdf_path.is_file():
        return True, (res.stdout or "OK").strip()
    return False, (res.stderr or res.stdout or "Error en typst_cv_engine").strip()


def find_chrome_binary() -> str | None:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def to_windows_path_if_needed(chrome_bin: str, path: Path) -> str:
    if chrome_bin.endswith(".exe"):
        res = subprocess.run(
            ["wslpath", "-w", str(path.resolve())],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return str(path.resolve())


def main():
    parser = argparse.ArgumentParser(
        description="Renderiza un CV Markdown a HTML ATS y PDF (Typst 1-página por defecto)."
    )
    parser.add_argument("cv_md", help="Ruta al archivo .md del CV")
    parser.add_argument(
        "--engine",
        choices=["auto", "typst", "html"],
        default="auto",
        help="Motor de PDF: 'auto' usa Typst 1-página si está disponible, con fallback a Chrome headless.",
    )
    parser.add_argument(
        "--layout",
        choices=["bento", "editorial", "tech-banner"],
        default=None,
        help="Forzar arquitectura visual (si se omite, se selecciona según `angulo` en frontmatter).",
    )
    parser.add_argument(
        "--angulo",
        default=None,
        help="Sobrescribir ángulo de diseño (finops|ai-automation|devsecops|secops-siem).",
    )
    args = parser.parse_args()

    md_path = Path(args.cv_md).resolve()
    if not md_path.exists():
        print(f"Error: No existe el archivo {md_path}")
        sys.exit(1)

    try:
        frontmatter, md_text = split_frontmatter(md_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"Error en {md_path}: {exc}")
        sys.exit(1)

    html_content = markdown_to_html(md_text)
    html_path = md_path.with_suffix(".html")
    pdf_path = md_path.with_suffix(".pdf")
    html_path.write_text(html_content, encoding="utf-8")
    print(f"[OK] HTML ATS generado: {html_path}")

    if args.engine in ("auto", "typst"):
        ok, info = render_with_typst_engine(
            md_path=md_path,
            pdf_path=pdf_path,
            angulo=args.angulo or frontmatter.get("angulo"),
            layout=args.layout,
        )
        if ok:
            print(f"[OK] PDF Typst 1-página ({info}) generado: {pdf_path}")
            return
        if args.engine == "typst":
            print(f"[ERROR] Falló motor Typst: {info}", file=sys.stderr)
            sys.exit(1)
        print(f"[WARN] Typst no disponible ({info}); usando fallback Chrome headless...")

    chrome_bin = find_chrome_binary()
    if chrome_bin:
        target_html = to_windows_path_if_needed(chrome_bin, html_path)
        target_pdf = to_windows_path_if_needed(chrome_bin, pdf_path)
        cmd = [
            chrome_bin,
            "--headless",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={target_pdf}",
            target_html,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if pdf_path.exists():
            print(f"[OK] PDF ATS (Chrome) generado: {pdf_path}")
        else:
            print(
                f"[WARN] Chrome no pudo exportar el PDF directamente ({res.stderr.strip()}). "
                f"Puedes abrir {html_path} y presionar Ctrl+P -> Guardar como PDF."
            )
    else:
        print(f"[INFO] HTML listo en {html_path} (abre en cualquier navegador y guarda como PDF).")


if __name__ == "__main__":
    main()
