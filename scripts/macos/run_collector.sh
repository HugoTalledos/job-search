#!/bin/bash
# Una corrida local de LinkedIn que guarda ofertas nuevas en Firestore.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
mkdir -p logs
exec >>"logs/collector-$(date +%Y-%m-%d).log" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') inicio buscador ==="

# Compartido con el comando anterior: nunca abrir dos sesiones MCP simultáneas.
LOCK="$REPO/.run.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    echo "Bloqueo abandonado (más de 2 h); se reemplaza."
    rm -rf "$LOCK" && mkdir "$LOCK"
  else
    echo "Ya hay una corrida en curso; se omite esta."
    exit 0
  fi
fi
trap 'rm -rf "$LOCK"' EXIT

PYTHON="$REPO/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "No existe .venv; ejecuta primero scripts/macos/setup.sh"
  exit 1
fi

"$PYTHON" -m job_agent collect "$@"
STATUS=$?
echo "Buscador terminó con código $STATUS"
find logs -name 'collector-*.log' -mtime +30 -delete 2>/dev/null
echo "=== $(date '+%Y-%m-%d %H:%M:%S') fin ==="
exit $STATUS
