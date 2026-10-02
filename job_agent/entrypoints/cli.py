"""CLI driving adapter: local collection and the legacy all-in-one cycle."""

from __future__ import annotations

import argparse
import logging
import sys

from ..adapters.persistence.json_store import JsonProfileStore
from ..bootstrap import build_collector, build_container, build_notifier, build_search_preferences
from ..config import load_config, load_dotenv
from ..domain.models import CollectorPlan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job_agent")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Search, match, tailor and notify (one cycle)")
    run_p.add_argument("--dry-run", action="store_true", help="Log notifications instead of sending them")
    run_p.add_argument("--refresh-profile", action="store_true", help="Rebuild the profile before searching")
    prof_p = sub.add_parser("profile", help="(Re)build the candidate profile and print it")
    prof_p.add_argument("--force", action="store_true")
    sub.add_parser("test-notify", help="Send a test notification")
    sub.add_parser("collect", help="Collect new LinkedIn postings into Firestore")
    sub.add_parser("seed-search-plan", help="Publish current search plan to Firestore")
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

    if args.command in {"collect", "seed-search-plan"}:
        try:
            cfg = load_config(args.config)
            if args.command == "seed-search-plan":
                stored = JsonProfileStore(cfg.storage.data_path / "profile.json").load()
                if stored is None:
                    raise ValueError("No existe data/profile.json; crea el perfil antes de publicar el plan")
            collector = build_collector(cfg)
            if args.command == "collect":
                report = collector.execute()
                for error in report.errors:
                    print(f"error: {error}", file=sys.stderr)
                return 1 if report.errors else 0
            plan = CollectorPlan(
                search=build_search_preferences(cfg).plan_for(stored.profile),
                max_details_per_run=cfg.search.max_details_per_run,
            )
            collector.store.save_plan(plan)
            print(f"Plan publicado en Firestore: {len(plan.search.queries)} consultas")
            return 0
        except ValueError as exc:
            print(f"Error de configuración: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            logging.exception("Collector command failed")
            print(f"error: {exc}", file=sys.stderr)
            return 1

    try:
        container = build_container(load_config(args.config), dry_run=getattr(args, "dry_run", False))
    except ValueError as exc:  # configuration problems (missing model id, API key...)
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    if args.command == "run":
        report = container.run_search_cycle.execute(refresh_profile=args.refresh_profile)
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    elif args.command == "profile":
        print(container.ensure_profile.execute(force=args.force).model_dump_json(indent=2))
    return 0
