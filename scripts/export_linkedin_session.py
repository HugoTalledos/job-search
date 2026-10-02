#!/usr/bin/env python3
"""Export your LinkedIn MCP session for GitHub Actions as a small secret (a few KB).

Run it on your own computer after `uvx mcp-server-linkedin@latest --login`:

    python scripts/export_linkedin_session.py               # writes linkedin_session.b64
    python scripts/export_linkedin_session.py --set-secret  # also stores it with the GitHub CLI (gh)

Only the two files the server needs on another machine are exported:
- ~/.linkedin-mcp/cookies.json   (LinkedIn cookies only)
- ~/.linkedin-mcp/source-state.json
The browser profile (~/.linkedin-mcp/profile, hundreds of MB) is NOT exported: on a different machine
the server rebuilds a fresh browser session from the cookies. To make sure it does that even when your
computer is also Linux x86-64 (same "runtime" as GitHub's runners), the exported source-state is marked
as coming from another runtime.

Uses only the Python standard library (Windows, macOS, Linux).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SECRET_NAME = "LINKEDIN_SESSION_B64"
GITHUB_SECRET_LIMIT = 48 * 1024
EXPORTED_RUNTIME_ID = "exported-session"


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def build_secret(root: Path) -> tuple[str, int]:
    cookies_path, state_path = root / "cookies.json", root / "source-state.json"
    if not cookies_path.exists() or not state_path.exists():
        sys.exit(
            f"No encontré una sesión en {root}.\n"
            "Primero inicia sesión con:  uvx mcp-server-linkedin@latest --login"
        )

    cookies = [c for c in json.loads(cookies_path.read_text()) if "linkedin.com" in c.get("domain", "")]
    if not any(c.get("name") == "li_at" for c in cookies):
        sys.exit("La sesión no tiene la cookie li_at (no hay sesión iniciada). Vuelve a ejecutar --login.")

    state = json.loads(state_path.read_text())
    state["source_runtime_id"] = EXPORTED_RUNTIME_ID  # always rebuild from cookies on the runner

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        _add(tar, ".linkedin-mcp/cookies.json", json.dumps(cookies).encode())
        _add(tar, ".linkedin-mcp/source-state.json", json.dumps(state, indent=2).encode())
        # The server requires a non-empty profile directory; its contents are not used on another runtime.
        _add(tar, ".linkedin-mcp/profile/.exported-session", b"exported for GitHub Actions\n")
    return base64.b64encode(buffer.getvalue()).decode(), len(cookies)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path.home() / ".linkedin-mcp", help="Carpeta de la sesión")
    parser.add_argument("--output", type=Path, default=Path("linkedin_session.b64"))
    parser.add_argument("--set-secret", action="store_true", help=f"Guardar {SECRET_NAME} con gh secret set")
    parser.add_argument("--repo", help="owner/repo (por defecto, el repo de la carpeta actual)")
    args = parser.parse_args()

    secret, n_cookies = build_secret(args.root.expanduser())
    if len(secret) > GITHUB_SECRET_LIMIT:
        sys.exit(f"El secret ocupa {len(secret)} bytes y GitHub admite máximo {GITHUB_SECRET_LIMIT}.")

    args.output.write_text(secret)
    try:
        args.output.chmod(0o600)
    except OSError:
        pass
    print(f"Listo: {args.output} ({len(secret)} caracteres, {n_cookies} cookies de LinkedIn).")
    print("Contiene tu sesión de LinkedIn: no lo subas al repositorio y bórralo cuando termines.")

    if not args.set_secret:
        print(f"\nPara guardarlo sin copiar y pegar:\n  gh secret set {SECRET_NAME} < {args.output}")
        print("O abre el archivo en un editor de texto, copia todo y pégalo como valor del secret.")
        return 0

    if not shutil.which("gh"):
        sys.exit("No encontré la GitHub CLI (gh). Instálala desde https://cli.github.com y ejecuta 'gh auth login'.")
    cmd = ["gh", "secret", "set", SECRET_NAME]
    if args.repo:
        cmd += ["--repo", args.repo]
    subprocess.run(cmd, input=secret, text=True, check=True)
    print(f"Secret {SECRET_NAME} guardado en GitHub.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
