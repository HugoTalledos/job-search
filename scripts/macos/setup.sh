#!/bin/bash
# Preparación única en macOS: entorno de Python, dependencias, .env y comprobaciones.
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

command -v python3 >/dev/null || { echo "Falta python3 (instálalo con: brew install python)"; exit 1; }
python3 - <<'PY' || { echo "Se necesita Python 3.10 o superior (brew install python)"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

if ! command -v uvx >/dev/null; then
  if command -v brew >/dev/null; then
    echo "Instalando uv (para lanzar el servidor MCP de LinkedIn)..."; brew install uv
  else
    echo "Falta uv: instálalo con 'brew install uv' o 'curl -LsSf https://astral.sh/uv/install.sh | sh'"; exit 1
  fi
fi

if command -v brew >/dev/null && ! brew list pango >/dev/null 2>&1; then
  echo "Instalando pango (necesario para generar las hojas de vida en PDF)..."
  brew install pango || echo "Aviso: sin pango las hojas de vida se enviarán en Markdown en vez de PDF."
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "Dependencias de Python instaladas en .venv"

if [ ! -f .env ]; then
  cp example.env .env && chmod 600 .env
  echo "Creé .env a partir de example.env: complétalo con tus claves."
fi

if [ ! -f "$HOME/.linkedin-mcp/cookies.json" ]; then
  echo
  echo "Falta iniciar sesión en LinkedIn para el agente. Ejecuta:"
  echo "  uvx mcp-server-linkedin@latest --login"
fi

case "$REPO" in
  "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*|*"Mobile Documents"*)
    echo
    echo "Aviso: el repo está en $REPO. macOS impide que las tareas programadas accedan a Documentos,"
    echo "Escritorio, Descargas o iCloud. Muévelo, por ejemplo a ~/dev/job-search, antes de programarlo."
    ;;
esac

echo
echo "Siguiente paso: instala el colector local: scripts/macos/setup_collector.sh"
