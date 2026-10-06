#!/usr/bin/env python3
"""Programa el colector local en macOS con launchd (3 corridas al día por defecto).

    python3 scripts/macos/install_schedule.py                       # 08:00, 14:00 y 22:00
    python3 scripts/macos/install_schedule.py --times 07:30 13:00 21:00
    python3 scripts/macos/install_schedule.py --dry-run             # solo muestra el plist
    python3 scripts/macos/install_schedule.py --uninstall

Además instala (con sudo) un LaunchDaemon que despierta el Mac un minuto antes de cada corrida
mediante `pmset schedule wake`, para que la suspensión no la interrumpa. --no-wake lo omite.
Si el Mac está apagado (no suspendido), la corrida no ocurre.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COLLECTOR_RUNNER = REPO / "scripts" / "macos" / "run_collector.sh"
PROTECTED = ("Documents", "Desktop", "Downloads", "Library/Mobile Documents")


def default_label(component: str = "collector") -> str:
    user = re.sub(r"[^a-z0-9]+", "", os.environ.get("USER", "user").lower()) or "user"
    return f"com.{user}.job-search-collector"


def parse_times(values: list[str]) -> list[dict[str, int]]:
    slots = []
    for value in values:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            sys.exit(f"Hora inválida: {value!r} (usa HH:MM, 24 h)")
        slots.append({"Hour": int(match[1]), "Minute": int(match[2])})
    return slots


def tool_path() -> str:
    """PATH for the job: launchd starts with a minimal one, but the agent needs uvx, git and python3."""
    dirs = [str(Path.home() / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
            "/usr/sbin", "/sbin"]
    if uvx := shutil.which("uvx"):
        dirs.insert(0, str(Path(uvx).parent))
    return ":".join(dict.fromkeys(dirs))


def build_plist(label: str, times: list[dict[str, int]], component: str = "collector") -> dict:
    logs = REPO / "logs"
    return {
        "Label": label,
        "ProgramArguments": ["/bin/bash", str(COLLECTOR_RUNNER)],
        "WorkingDirectory": str(REPO),
        "StartCalendarInterval": times,
        "EnvironmentVariables": {"PATH": tool_path(), "HOME": str(Path.home()), "LANG": "en_US.UTF-8"},
        "StandardOutPath": str(logs / "launchd.log"),
        "StandardErrorPath": str(logs / "launchd.log"),
        "RunAtLoad": False,
        "ProcessType": "Standard",
    }


def wake_times(times: list[dict[str, int]]) -> list[dict[str, int]]:
    """Un minuto antes de cada corrida (00:00 -> 23:59)."""
    minutes = [(t["Hour"] * 60 + t["Minute"] - 1) % 1440 for t in times]
    return [{"Hour": m // 60, "Minute": m % 60} for m in minutes]


def wake_script(owner: str, times: list[dict[str, int]]) -> str:
    """Programa (idempotente) los despertares de hoy y mañana que aún no pasaron y mantiene el Mac
    despierto 5 min para que launchd lance la corrida. Corre en cada despertar, así la cadena no se corta.

    En el último despertar del día lo mantiene 7 min y luego lo suspende, cuando el colector ya terminó
    y nadie usó el teclado o el ratón en los últimos 5 min."""
    slots = [f"{t['Hour']:02d}:{t['Minute']:02d}" for t in times]
    return f"""now=$(date +%s)
for day in 0 1; do
  for hm in {" ".join(slots)}; do
    when="$(date -v+${{day}}d +%m/%d/%y) $hm:00"
    [ "$(date -j -f '%m/%d/%y %H:%M:%S' "$when" +%s)" -gt "$now" ] || continue
    pmset schedule cancel wake "$when" {owner} 2>/dev/null
    pmset schedule wake "$when" {owner}
  done
done
if [ "$(date +%H:%M)" != "{max(slots)}" ]; then
  caffeinate -i -t 300
  exit 0
fi
caffeinate -i -t 420
while pgrep -f local_collector >/dev/null; do sleep 30; done
idle=$(ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {{print int($NF / 1000000000); exit}}')
[ "${{idle:-0}}" -ge 300 ] && pmset sleepnow
exit 0
"""


def build_wake_plist(label: str, times: list[dict[str, int]]) -> dict:
    wake_label = f"{label}.wake"
    wakes = wake_times(times)
    return {
        "Label": wake_label,
        "ProgramArguments": ["/bin/sh", "-c", wake_script(wake_label, wakes)],
        "StartCalendarInterval": wakes,
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        "RunAtLoad": True,
    }


def install_wake(label: str, times: list[dict[str, int]]) -> bool:
    """Instala el LaunchDaemon de root (pmset schedule requiere root). Pide la contraseña con sudo."""
    wake_label = f"{label}.wake"
    target = f"/Library/LaunchDaemons/{wake_label}.plist"
    staged = REPO / "logs" / f"{wake_label}.plist"
    staged.write_bytes(plistlib.dumps(build_wake_plist(label, times)))
    print("Para despertar el Mac antes de cada corrida se necesita sudo (pmset).")
    steps = [
        ["launchctl", "bootout", f"system/{wake_label}"],
        ["install", "-m", "644", "-o", "root", "-g", "wheel", str(staged), target],
        ["launchctl", "bootstrap", "system", target],
    ]
    try:
        for i, step in enumerate(steps):
            result = subprocess.run(["sudo", *step], capture_output=True, text=True)
            if result.returncode != 0 and i > 0:
                print(f"No se pudo programar el despertar ({' '.join(step)}): {result.stderr.strip()}")
                return False
    finally:
        staged.unlink(missing_ok=True)
    return True


def uninstall_wake(label: str) -> None:
    wake_label = f"{label}.wake"
    target = Path(f"/Library/LaunchDaemons/{wake_label}.plist")
    if not target.exists():
        return
    subprocess.run(["sudo", "launchctl", "bootout", f"system/{wake_label}"], capture_output=True)
    subprocess.run(["sudo", "rm", "-f", str(target)], capture_output=True)
    sched = subprocess.run(["pmset", "-g", "sched"], capture_output=True, text=True).stdout
    for when in re.findall(rf"wake at (\S+ \S+) by '{re.escape(wake_label)}'", sched):
        date, clock = when.split()
        month, day, year = date.split("/")
        subprocess.run(["sudo", "pmset", "schedule", "cancel", "wake", f"{month}/{day}/{year[-2:]} {clock}",
                        wake_label], capture_output=True)


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def preflight() -> list[str]:
    problems = []
    if not (REPO / ".venv-collector" / "bin" / "python").exists():
        problems.append("No existe .venv-collector: ejecuta primero scripts/macos/setup_collector.sh")
    if not (REPO / ".env").exists():
        problems.append("No existe .env: cópialo de example.env (cp example.env .env) y complétalo")
    if not shutil.which("uvx"):
        problems.append("No encuentro uvx: instálalo con 'brew install uv'")
    if not (Path.home() / ".linkedin-mcp" / "cookies.json").exists():
        problems.append("No hay sesión de LinkedIn: ejecuta 'uvx mcp-server-linkedin@latest --login'")
    home = Path.home()
    if any(REPO.is_relative_to(home / p) for p in PROTECTED):
        problems.append(
            f"El repo está en {REPO}: macOS impide a las tareas programadas acceder a Documentos, "
            "Escritorio, Descargas o iCloud. Usa otra ubicación (p. ej. ~/dev/job-search) y vuelve a "
            "ejecutar este instalador."
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--times", nargs="+", default=["08:00", "14:00", "22:00"], help="Horas HH:MM (24 h)")
    parser.add_argument("--component", choices=["collector"], default="collector")
    parser.add_argument("--label")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar el plist sin instalar nada")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-wake", action="store_true", help="No despertar el Mac antes de cada corrida")
    args = parser.parse_args()
    label = args.label or default_label(args.component)

    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    domain = f"gui/{os.getuid()}"

    if args.dry_run:
        sys.stdout.write(plistlib.dumps(build_plist(label, parse_times(args.times), args.component)).decode())
        return 0
    if sys.platform != "darwin":
        sys.exit("Este instalador es para macOS.")

    if args.uninstall:
        launchctl("bootout", domain, str(plist_path), check=False)
        plist_path.unlink(missing_ok=True)
        uninstall_wake(label)
        print(f"Programación eliminada ({label}).")
        return 0

    if problems := preflight():
        print("Antes de programar el agente:\n- " + "\n- ".join(problems))
        return 1

    (REPO / "logs").mkdir(exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(build_plist(label, parse_times(args.times), args.component)))
    launchctl("bootout", domain, str(plist_path), check=False)  # replace a previous installation
    result = launchctl("bootstrap", domain, str(plist_path), check=False)
    if result.returncode != 0:
        sys.exit(f"launchctl bootstrap falló: {result.stderr.strip()}")
    launchctl("enable", f"{domain}/{label}", check=False)

    print(f"Agente programado a las {', '.join(args.times)} ({label}).")
    if not args.no_wake and install_wake(label, parse_times(args.times)):
        print("  El Mac se despertará un minuto antes de cada corrida (ver: pmset -g sched).")
    print(f"  Correr ahora:     launchctl kickstart {domain}/{label}")
    print(f"  Ver el log:       tail -f {REPO}/logs/collector-$(date +%Y-%m-%d).log")
    print(f"  Estado:           launchctl print {domain}/{label} | head -20")
    print(f"  Desinstalar:      python3 scripts/macos/install_schedule.py --component {args.component} --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
