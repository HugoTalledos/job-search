#!/usr/bin/env python3
"""Programa el colector local en macOS con launchd (3 corridas al día por defecto).

    python3 scripts/macos/install_schedule.py                       # 08:00, 14:00 y 22:00
    python3 scripts/macos/install_schedule.py --times 07:30 13:00 21:00
    python3 scripts/macos/install_schedule.py --dry-run             # solo muestra el plist
    python3 scripts/macos/install_schedule.py --uninstall

launchd ejecuta una corrida pendiente al despertar si el Mac estaba suspendido a la hora programada
(varias pendientes se unen en una). Si estaba apagado, esa corrida no ocurre.
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
    print(f"  Correr ahora:     launchctl kickstart {domain}/{label}")
    print(f"  Ver el log:       tail -f {REPO}/logs/collector-$(date +%Y-%m-%d).log")
    print(f"  Estado:           launchctl print {domain}/{label} | head -20")
    print(f"  Desinstalar:      python3 scripts/macos/install_schedule.py --component {args.component} --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
