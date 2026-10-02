"""CLI driving adapter: python -m job_agent {run,profile,test-notify}"""

from __future__ import annotations

import argparse
import logging
import sys

from ..bootstrap import build_container, build_notifier
from ..config import load_config, load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_agent")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Search, match, tailor and notify (one cycle)")
    run_p.add_argument("--dry-run", action="store_true", help="Log notifications instead of sending them")
    prof_p = sub.add_parser("profile", help="(Re)build the candidate profile and print it")
    prof_p.add_argument("--force", action="store_true")
    sub.add_parser("test-notify", help="Send a test notification")
    args = parser.parse_args(argv)
    load_dotenv()  # local runs keep their secrets in .env; in GitHub Actions they come from the environment

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the Telegram bot token
    if args.command == "test-notify":  # needs neither the LLM nor the job sources
        notifier = build_notifier(dry_run=False)
        send_text = getattr(notifier, "send_text", None)
        if send_text is None:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set", file=sys.stderr)
            return 1
        send_text("✅ job-search agent: notificaciones configuradas correctamente.")
        return 0

    try:
        container = build_container(load_config(args.config), dry_run=getattr(args, "dry_run", False))
    except ValueError as exc:  # configuration problems (missing model id, API key...)
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    if args.command == "run":
        report = container.run_search_cycle.execute()
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    elif args.command == "profile":
        print(container.ensure_profile.execute(force=args.force).model_dump_json(indent=2))
    return 0
