#!/bin/bash
# Install only the local LinkedIn collector's Python dependencies.
set -eu
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

command -v python3 >/dev/null || { echo "Falta python3"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Se necesita Python 3.10 o superior"; exit 1;
}
command -v uvx >/dev/null || { echo "Falta uvx; instala uv e inicia sesión en LinkedIn"; exit 1; }
[ -d .venv-collector ] || python3 -m venv .venv-collector
.venv-collector/bin/pip install -r local_collector/requirements.txt

if [ ! -f .env ]; then
  cp example.env .env
  chmod 600 .env
  echo "Creé .env: configura FIRESTORE_PROJECT_ID y GOOGLE_APPLICATION_CREDENTIALS."
fi
echo "Buscador listo. Ejecuta scripts/macos/run_collector.sh"
