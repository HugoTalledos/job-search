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
WAITED=0
while ! mkdir "$LOCK" 2>/dev/null; do
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    echo "Bloqueo abandonado (más de 2 h); se reemplaza."
    rm -rf "$LOCK"
    continue
  fi
  if [ "$WAITED" -ge 7200 ]; then
    echo "La otra corrida no terminó tras 2 h; esta corrida falla."
    exit 1
  fi
  sleep 1
  WAITED=$((WAITED + 1))
done
trap 'rm -rf "$LOCK"' EXIT

PYTHON="$REPO/.venv-collector/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "No existe .venv-collector; ejecuta primero scripts/macos/setup_collector.sh"
  exit 1
fi

"$PYTHON" -m local_collector "$@"
STATUS=$?
echo "Buscador terminó con código $STATUS"
find logs -name 'collector-*.log' -mtime +30 -delete 2>/dev/null
echo "=== $(date '+%Y-%m-%d %H:%M:%S') fin ==="
exit $STATUS
