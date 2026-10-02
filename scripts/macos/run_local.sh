#!/bin/bash
# Una corrida del agente en este Mac. La lanza launchd (ver install_schedule.py) o tú a mano:
#   scripts/macos/run_local.sh            # corrida normal
#   scripts/macos/run_local.sh --dry-run  # sin enviar notificaciones
# Escribe el log en logs/run-AAAA-MM-DD.log. El estado y las hojas de vida quedan solo en este Mac
# (storage en config.yaml); no se suben al repositorio.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 1
mkdir -p logs
exec >>"logs/run-$(date +%Y-%m-%d).log" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') inicio ==="

# Evita dos corridas a la vez (p. ej. una manual mientras corre la programada).
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

"$PYTHON" -m job_agent run "$@"
STATUS=$?
echo "Agente terminó con código $STATUS"

find logs -name 'run-*.log' -mtime +30 -delete 2>/dev/null
echo "=== $(date '+%Y-%m-%d %H:%M:%S') fin ==="
exit $STATUS
