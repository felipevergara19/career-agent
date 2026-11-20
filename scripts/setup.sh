#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "=== Career Agent — Setup inicial ==="

# 1. Crear carpetas locales con los permisos de tu usuario (evita que Docker las cree como root)
mkdir -p perfil/documentos ofertas cvs-generados/base postulaciones/material entrevistas logs .locks

# 2. Copiar plantillas de perfil si aún no existen
if [[ ! -f perfil/perfil-maestro.md && -f perfil/perfil-maestro.example.md ]]; then
  cp perfil/perfil-maestro.example.md perfil/perfil-maestro.md
  echo "✓ Creado perfil/perfil-maestro.md desde plantilla (edítalo con tu experiencia real)"
fi

if [[ ! -f perfil/preferencias.md && -f perfil/preferencias.example.md ]]; then
  cp perfil/preferencias.example.md perfil/preferencias.md
  echo "✓ Creado perfil/preferencias.md desde plantilla (ajusta tu país, salario y rúbrica)"
fi

# 3. Generar .env con tu UID/GID local si no existe
if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  USER_UID="$(id -u)"
  USER_GID="$(id -g)"
  sed -i "s/^UID=.*/UID=${USER_UID}/" .env
  sed -i "s/^GID=.*/GID=${USER_GID}/" .env
  echo "✓ Creado .env con UID=${USER_UID} GID=${USER_GID}"
fi

echo ""
echo "Siguiente paso:"
echo "  1. Edita perfil/perfil-maestro.md y perfil/preferencias.md con tus datos."
echo "  2. Levanta el stack Docker:"
echo "       docker compose up -d --build"
echo "  3. Sincroniza las bases de datos la primera vez:"
echo "       docker compose exec -T career-app python3 scripts/db_sync.py --all"
echo "  4. Abre el Command Center en http://localhost:8000 y pega tu API key en 'API Keys & IA'."
