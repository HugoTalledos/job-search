"""CLI: python -m job_agent {run,profile,test-notify}"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_agent")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Search, match, tailor and notify (one cycle)")
    run_p.add_argument("--dry-run", action="store_true", help="Do not send notifications")
    prof_p = sub.add_parser("profile", help="(Re)build the candidate profile and print it")
    prof_p.add_argument("--force", action="store_true")
    sub.add_parser("test-notify", help="Send a test notification")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the Telegram bot token
    cfg = load_config(args.config)

    if args.command == "run":
        from .pipeline import run

        run(cfg, dry_run=args.dry_run)
    elif args.command == "profile":
        from .profile import ensure_profile

        print(ensure_profile(cfg, force=args.force).model_dump_json(indent=2))
    elif args.command == "test-notify":
        from .notifier import Notifier

        notifier = Notifier()
        if not notifier.enabled:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set", file=sys.stderr)
            return 1
        notifier.send("✅ job-search agent: notificaciones configuradas correctamente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
