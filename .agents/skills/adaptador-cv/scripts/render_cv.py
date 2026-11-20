#!/usr/bin/env python3
"""Convierte un CV en Markdown (cvs-generados/.../*.md) a HTML optimizado para ATS y PDF.

Usa Google Chrome en modo headless si está disponible en Linux o WSL2 Windows.
"""

from pathlib import Path
import re
import shutil
import subprocess
import sys


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
  # Links [label](url)
  text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
  # Bold **text**
  text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
  # Italic *text*
  text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
  return text


def strip_frontmatter(md_text: str) -> str:
  """Quita el bloque YAML inicial (--- ... ---) que usa el protocolo multi-agente."""
  if not md_text.startswith("---\n"):
    return md_text
  end = md_text.find("\n---\n", 4)
  if end == -1:
    raise ValueError("Frontmatter YAML sin cierre '---'")
  return md_text[end + 5:].lstrip("\n")


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
  return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CV - Felipe Vergara Peñaloza</title>
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
  if len(sys.argv) < 2:
    print("Uso: python3 render_cv.py <ruta-al-cv.md>")
    sys.exit(1)

  md_path = Path(sys.argv[1]).resolve()
  if not md_path.exists():
    print(f"Error: No existe el archivo {md_path}")
    sys.exit(1)

  try:
    md_text = strip_frontmatter(md_path.read_text(encoding="utf-8"))
  except ValueError as exc:
    print(f"Error en {md_path}: {exc}")
    sys.exit(1)
  html_content = markdown_to_html(md_text)

  html_path = md_path.with_suffix(".html")
  pdf_path = md_path.with_suffix(".pdf")
  html_path.write_text(html_content, encoding="utf-8")
  print(f"[OK] HTML ATS generado: {html_path}")

  chrome_bin = find_chrome_binary()
  if chrome_bin:
    target_html = to_windows_path_if_needed(chrome_bin, html_path)
    target_pdf = to_windows_path_if_needed(chrome_bin, pdf_path)
    cmd = [
        chrome_bin,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={target_pdf}",
        target_html,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if pdf_path.exists():
      print(f"[OK] PDF ATS generado: {pdf_path}")
    else:
      print(
          f"[WARN] Chrome no pudo exportar el PDF directamente ({res.stderr.strip()}). "
          f"Puedes abrir {html_path} y presionar Ctrl+P -> Guardar como PDF."
      )
  else:
    print(
        f"[INFO] HTML listo en {html_path} (abre en cualquier navegador y guarda como PDF)."
    )


if __name__ == "__main__":
  main()
