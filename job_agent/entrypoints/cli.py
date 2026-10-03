"""CLI driving adapter: Telegram webhook registration."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from ..adapters.notifications.telegram_profile import register_webhook
from ..config import load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_agent")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    hook_p = sub.add_parser("set-telegram-webhook", help="Point the Telegram bot at the agent's webhook")
    hook_p.add_argument("url", help="Public HTTPS base URL of the service, e.g. https://agent.example.com")
    args = parser.parse_args(argv)
    load_dotenv()  # local runs keep their secrets in .env; in GitHub Actions they come from the environment

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines include the Telegram bot token
    if args.command == "set-telegram-webhook":
        token, secret = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        if not token.strip() or not secret.strip():
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET not set", file=sys.stderr)
            return 2
        if not args.url.startswith("https://"):
            print("Telegram only calls HTTPS webhooks", file=sys.stderr)
            return 2
        register_webhook(token, args.url.rstrip("/") + "/webhooks/telegram", secret)
        print("Webhook de Telegram configurado")
        return 0
    return 0
