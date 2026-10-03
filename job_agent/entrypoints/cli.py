"""CLI driving adapter: Telegram webhook registration."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from ..adapters.notifications.telegram_profile import register_webhook
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import firestore
from pydantic import ValidationError

from ..adapters.persistence import FirestoreProfileStore, FirestoreSearchSettingsStore
from ..application import ManageSearchPreferences
from ..config import ROOT, legacy_search_preferences, load_config, load_dotenv
from ..domain.policies import SearchBudgets


def build_search_preferences_manager(budgets: SearchBudgets) -> ManageSearchPreferences:
    project = os.environ.get("FIRESTORE_PROJECT_ID", "").strip()
    if not project:
        raise ValueError("FIRESTORE_PROJECT_ID no está configurado")
    try:
        client = firestore.Client(project=project)
    except DefaultCredentialsError as exc:
        raise ValueError("No se encontraron credenciales de Firestore (GOOGLE_APPLICATION_CREDENTIALS)") from exc
    return ManageSearchPreferences(
        store=FirestoreSearchSettingsStore(client), profiles=FirestoreProfileStore(client), budgets=budgets
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_agent")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    hook_p = sub.add_parser("set-telegram-webhook", help="Point the Telegram bot at the agent's webhook")
    hook_p.add_argument("url", help="Public HTTPS base URL of the service, e.g. https://agent.example.com")
    seed_p = sub.add_parser("seed-search-preferences", help="Publish the initial search preferences to Firestore")
    seed_p.add_argument("--from-config", help="YAML file with the legacy search keys (default: the active config)")
    seed_p.add_argument("--force", action="store_true", help="Replace preferences that already exist")
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
    if args.command == "seed-search-preferences":
        try:
            source = args.from_config or args.config or os.environ.get("JOB_AGENT_CONFIG") or ROOT / "config.yaml"
            preferences = legacy_search_preferences(Path(source))
            budgets = load_config(args.config).search_budgets()
            created = build_search_preferences_manager(budgets).seed(preferences, force=args.force)
        except (ValueError, ValidationError) as exc:
            print(f"Error de configuración: {exc}", file=sys.stderr)
            return 2
        print(
            "Preferencias publicadas en Firestore"
            if created
            else "Ya existen preferencias; usa --force para reemplazarlas"
        )
        return 0
    return 0
