#!/usr/bin/env python3
"""Sincroniza ofertas, CVs, material y registros con Google Drive vía rclone.

Solo sube archivos nuevos o modificados (manifiesto de hashes en
postulaciones/.drive-sync.json), así nunca duplica subidas. `rclone copyto`
sobre un archivo existente actualiza su contenido conservando el ID/link de Drive.

Configuración única (cuenta PERSONAL, nunca la de Colektia):
  1. Instalar rclone:      curl https://rclone.org/install.sh | sudo bash
  2. Crear el remoto:      rclone config
       name> gdrive-career   storage> drive   scope> drive.file (o drive)
       root_folder_id> 14cw39jfmxeHC_bgV5fcxz7PKf3BNPtt3   (carpeta Career-Agent-Felipe)
  3. Probar:               python3 scripts/drive_sync.py --dry-run

Variables: CAREER_DRIVE_REMOTE (por defecto "gdrive-career").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import estado  # noqa: E402

ROOT = estado.ROOT
MANIFIESTO = ROOT / "postulaciones" / ".drive-sync.json"
REMOTO = os.environ.get("CAREER_DRIVE_REMOTE", "gdrive-career")

# (patrón glob local, función → ruta destino en Drive relativa a la carpeta raíz)
MAPEO = [
    ("ofertas/*/*.md", lambda p: f"Ofertas-Evaluadas/{p.parent.name}/{p.name}"),
    ("cvs-generados/*/*.pdf", lambda p: f"CVs-Generados/{p.parent.name}/{p.name}"),
    ("cvs-generados/*/*.md", lambda p: f"CVs-Generados/{p.parent.name}/{p.name}"),
    ("postulaciones/material/*.md", lambda p: f"Material-Postulacion/{p.name}"),
    ("entrevistas/*.md", lambda p: f"Entrevistas/{p.name}"),
    ("postulaciones/control-carrera-felipe.xlsx", lambda p: p.name),
    ("postulaciones/tracker.md", lambda p: p.name),
    ("postulaciones/estado-postulaciones.csv", lambda p: p.name),
    ("postulaciones/mejoras-perfil.md", lambda p: p.name),
    ("perfil/perfil-maestro.md", lambda p: p.name),
]


def sha256(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def candidatos() -> list[tuple[Path, str]]:
  vistos, out = set(), []
  for patron, destino in MAPEO:
    for p in sorted(ROOT.glob(patron)):
      if p.is_file() and p not in vistos:
        vistos.add(p)
        out.append((p, destino(p)))
  return out


def verificar_rclone() -> str | None:
  if not shutil.which("rclone"):
    return "rclone no está instalado (ver instrucciones en scripts/drive_sync.py)"
  res = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True)
  if f"{REMOTO}:" not in res.stdout.split():
    return f"el remoto '{REMOTO}:' no existe; créalo con `rclone config` (ver docstring)"
  return None


def main() -> int:
  p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument("--dry-run", action="store_true", help="muestra qué subiría, sin subir")
  p.add_argument("--forzar", action="store_true", help="ignora el manifiesto y sube todo")
  a = p.parse_args()

  manifiesto = json.loads(MANIFIESTO.read_text(encoding="utf-8")) if MANIFIESTO.exists() else {}
  pendientes = []
  for local, destino in candidatos():
    rel = local.relative_to(ROOT).as_posix()
    h = sha256(local)
    if a.forzar or manifiesto.get(rel, {}).get("sha256") != h:
      pendientes.append((local, rel, destino, h))

  print(f"Drive ({REMOTO}:) — {len(pendientes)} archivo(s) nuevos o modificados")
  for _, rel, destino, _ in pendientes:
    print(f"  ↑ {rel}  →  {destino}")
  if a.dry_run or not pendientes:
    return 0

  error = verificar_rclone()
  if error:
    print(f"[ERROR] {error}")
    return 1

  fallos = 0
  with estado.lock():
    for local, rel, destino, h in pendientes:
      res = subprocess.run(["rclone", "copyto", str(local), f"{REMOTO}:{destino}"], capture_output=True, text=True)
      if res.returncode != 0:
        print(f"[ERROR] {rel}: {res.stderr.strip()[:300]}")
        fallos += 1
        continue
      manifiesto[rel] = {"sha256": h, "destino": destino, "subido": estado.hoy()}
    estado.escribir_atomico(MANIFIESTO, json.dumps(manifiesto, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
  print(f"✓ Subidos {len(pendientes) - fallos}/{len(pendientes)}")
  return 1 if fallos else 0


if __name__ == "__main__":
  sys.exit(main())
