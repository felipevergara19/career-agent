#!/usr/bin/env python3
"""Genera y actualiza el archivo Excel maestro (postulaciones/control-carrera-felipe.xlsx)

y los archivos CSV a partir del registro JSON/estructurado en
postulaciones/datos-historial.json.
No requiere librerías externas (genera .xlsx nativo OpenXML con múltiples hojas
+ CSVs).
"""

import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

BASE_DIR = Path(__file__).resolve().parents[4]
POSTULACIONES_DIR = BASE_DIR / "postulaciones"
DATA_JSON = POSTULACIONES_DIR / "datos-historial.json"
XLSX_PATH = POSTULACIONES_DIR / "control-carrera-felipe.xlsx"
CSV_OFERTAS = POSTULACIONES_DIR / "ofertas-y-postulaciones.csv"
CSV_SKILLS = POSTULACIONES_DIR / "radar-mercado-skills.csv"


def col_letter(idx: int) -> str:
  """Convierte índice 0-based (0 -> A, 25 -> Z, 26 -> AA)."""
  result = ""
  idx += 1
  while idx > 0:
    idx, rem = divmod(idx - 1, 26)
    result = chr(65 + rem) + result
  return result


def build_sheet_xml(rows: list[list[str | int | float]]) -> str:
  xml_rows = []
  for r_idx, row in enumerate(rows, start=1):
    cells = []
    style_attr = ' s="1"' if r_idx == 1 else ""
    for c_idx, val in enumerate(row):
      ref = f"{col_letter(c_idx)}{r_idx}"
      if isinstance(val, (int, float)) and not isinstance(val, bool):
        cells.append(f'<c r="{ref}"{style_attr}><v>{val}</v></c>')
      else:
        text = escape(str(val if val is not None else ""))
        cells.append(
            f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'
        )
    xml_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

  return (
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<worksheet'
      ' xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      f'<sheetData>{"".join(xml_rows)}</sheetData>'
      "</worksheet>"
  )


def write_xlsx(
    filepath: Path, sheets: list[tuple[str, list[list[str | int | float]]]]
) -> None:
  content_types = [
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
      (
          '<Types'
          ' xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      ),
      (
          '<Default Extension="rels"'
          ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      ),
      '<Default Extension="xml" ContentType="application/xml"/>',
      (
          '<Override PartName="/xl/workbook.xml"'
          ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
      ),
      (
          '<Override PartName="/xl/styles.xml"'
          ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
      ),
  ]
  for i in range(1, len(sheets) + 1):
    content_types.append(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    )
  content_types.append("</Types>")

  rels = (
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<Relationships'
      ' xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
      '<Relationship Id="rId1"'
      ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
      ' Target="xl/workbook.xml"/>'
      "</Relationships>"
  )

  wb_sheets = []
  wb_rels = [
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
      (
          '<Relationships'
          ' xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
      ),
  ]
  for i, (name, _) in enumerate(sheets, start=1):
    safe_name = escape(name[:31])
    wb_sheets.append(
        f'<sheet name="{safe_name}" sheetId="{i}" r:id="rId{i}"/>'
    )
    wb_rels.append(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        f' Target="worksheets/sheet{i}.xml"/>'
    )
  wb_rels.append(
      f'<Relationship Id="rId{len(sheets) + 1}" '
      'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"'
      ' Target="styles.xml"/>'
  )
  wb_rels.append("</Relationships>")

  workbook_xml = (
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<workbook'
      ' xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
      f'<sheets>{"".join(wb_sheets)}</sheets>'
      "</workbook>"
  )

  styles_xml = (
      '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
      '<styleSheet'
      ' xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      '<fonts count="2">'
      '<font><sz val="11"/><name val="Calibri"/></font>'
      '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name'
      ' val="Calibri"/></font>'
      "</fonts>"
      '<fills count="3">'
      '<fill><patternFill patternType="none"/></fill>'
      '<fill><patternFill patternType="gray125"/></fill>'
      '<fill><patternFill patternType="solid"><fgColor'
      ' rgb="FF1E293B"/></patternFill></fill>'
      "</fills>"
      '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
      '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0"'
      ' borderId="0"/></cellStyleXfs>'
      '<cellXfs count="2">'
      '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
      '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0"'
      ' applyFont="1" applyFill="1"/>'
      "</cellXfs>"
      "</styleSheet>"
  )

  with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("[Content_Types].xml", "".join(content_types))
    zf.writestr("_rels/.rels", rels)
    zf.writestr("xl/workbook.xml", workbook_xml)
    zf.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
    zf.writestr("xl/styles.xml", styles_xml)
    for i, (_, rows) in enumerate(sheets, start=1):
      zf.writestr(f"xl/worksheets/sheet{i}.xml", build_sheet_xml(rows))


def main() -> None:
  if not DATA_JSON.exists():
    print(f"Error: No se encontró {DATA_JSON}")
    return

  data = json.loads(DATA_JSON.read_text(encoding="utf-8"))

  ofertas_headers = [
      "ID",
      "Fecha Búsqueda",
      "Empresa",
      "Cargo / Rol",
      "Portal / Donde se encontró",
      "URL / Link",
      "Modalidad y Ubicación",
      "Idioma",
      "Salario Estimado",
      "Match Score (0-100)",
      "Prioridad",
      "Estado Postulación",
      "CV Utilizado (Archivo)",
      "Ángulo de CV Usado",
      "Keywords Principales Pedidas",
      "Brechas Detectadas en la Oferta",
      "Resultado / Notas de Seguimiento",
  ]
  ofertas_rows = [ofertas_headers]
  for item in data.get("ofertas", []):
    ofertas_rows.append([
        item.get("id", ""),
        item.get("fecha", ""),
        item.get("empresa", ""),
        item.get("cargo", ""),
        item.get("portal", ""),
        item.get("url", ""),
        item.get("modalidad", ""),
        item.get("idioma", ""),
        item.get("salario", ""),
        item.get("match_score", 0),
        item.get("prioridad", ""),
        item.get("estado", ""),
        item.get("cv_usado", ""),
        item.get("angulo_cv", ""),
        ", ".join(item.get("keywords", [])),
        ", ".join(item.get("brechas", [])),
        item.get("notas", ""),
    ])

  skills_headers = [
      "Skill / Tecnología / Certificación",
      "Categoría",
      "Nivel Actual Felipe",
      "Veces Pedida en Ofertas",
      "% de Ofertas que la Piden",
      "Empresas donde apareció",
      "Acción Recomendada para Avanzar en el Perfil",
  ]
  skills_rows = [skills_headers]
  for sk in data.get("radar_skills", []):
    skills_rows.append([
        sk.get("skill", ""),
        sk.get("categoria", ""),
        sk.get("nivel_actual", ""),
        sk.get("frecuencia", 0),
        sk.get("porcentaje", ""),
        ", ".join(sk.get("empresas", [])),
        sk.get("accion_recomendada", ""),
    ])

  portales_headers = [
      "Portal / Fuente de Búsqueda",
      "Ofertas Totales Vistas",
      "Ofertas Válidas (100% Remoto >=65 pts)",
      "Ofertas Descartadas",
      "Promedio Match Score",
      "Tipos de Roles Encontrados",
      "Evaluación del Portal",
  ]
  portales_rows = [portales_headers]
  for p in data.get("efectividad_portales", []):
    portales_rows.append([
        p.get("portal", ""),
        p.get("vistas", 0),
        p.get("validas", 0),
        p.get("descartadas", 0),
        p.get("promedio_match", 0),
        p.get("roles", ""),
        p.get("evaluacion", ""),
    ])

  write_xlsx(
      XLSX_PATH,
      [
          ("1_Ofertas_y_Postulaciones", ofertas_rows),
          ("2_Radar_Mercado_Skills", skills_rows),
          ("3_Portales_y_Efectividad", portales_rows),
      ],
  )

  with CSV_OFERTAS.open("w", newline="", encoding="utf-8-sig") as f:
    csv.writer(f).writerows(ofertas_rows)

  with CSV_SKILLS.open("w", newline="", encoding="utf-8-sig") as f:
    csv.writer(f).writerows(skills_rows)

  print(f"[OK] Excel multi-hoja actualizado: {XLSX_PATH}")
  print(f"[OK] CSV de ofertas actualizado:   {CSV_OFERTAS}")
  print(f"[OK] CSV de skills actualizado:    {CSV_SKILLS}")


if __name__ == "__main__":
  main()
