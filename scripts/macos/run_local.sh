#!/bin/bash
# Una corrida del agente en este Mac. La lanza launchd (ver install_schedule.py) o tú a mano:
#   scripts/macos/run_local.sh            # corrida normal
#   scripts/macos/run_local.sh --dry-run  # sin enviar notificaciones
# Escribe el log en logs/run-AAAA-MM-DD.log. Si JOB_AGENT_GIT_SYNC=1 en .env, trae los últimos cambios del
# repo antes de correr y sube data/ y output/ al terminar.
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

SYNC=0
if [ -f .env ]; then
  SYNC="$(grep -E '^[[:space:]]*JOB_AGENT_GIT_SYNC=' .env | tail -1 | cut -d= -f2 | tr -d '"'"'"' ')"
fi

if [ "$SYNC" = "1" ]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  git pull --rebase --autostash -q origin "$BRANCH" || echo "Aviso: no se pudo actualizar desde GitHub; se continúa con la copia local."
  # Para que las notificaciones incluyan el enlace a la hoja de vida en GitHub.
  REMOTE="$(git remote get-url origin 2>/dev/null)"
  case "$REMOTE" in
    https://github.com/*|git@github.com:*)
      SLUG="$(echo "$REMOTE" | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##')"
      export GITHUB_SERVER_URL="https://github.com" GITHUB_REPOSITORY="$SLUG" GITHUB_REF_NAME="$BRANCH"
      ;;
  esac
fi

"$PYTHON" -m job_agent run "$@"
STATUS=$?
echo "Agente terminó con código $STATUS"

if [ "$SYNC" = "1" ]; then
  git add data output
  if ! git diff --cached --quiet -- data output; then
    # Solo data/ y output/: nunca otros cambios tuyos que estén en preparación.
    git commit -q -m "chore(job-search): resultados $(date -u +%Y-%m-%dT%H:%MZ)" -- data output
    for attempt in 1 2 3; do
      if git push -q origin "$BRANCH"; then echo "Resultados subidos a GitHub."; break; fi
      echo "Push falló (intento $attempt); reintentando..."
      git pull --rebase --autostash -q origin "$BRANCH"
      sleep $((attempt * 5))
    done
  fi
fi

find logs -name 'run-*.log' -mtime +30 -delete 2>/dev/null
echo "=== $(date '+%Y-%m-%d %H:%M:%S') fin ==="
exit $STATUS
