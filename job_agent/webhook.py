"""ASGI composition: affinity scoring webhook and the Telegram bot that controls the agent."""

from __future__ import annotations

import os
import logging
from threading import Lock

from fastapi import FastAPI
from google.cloud import firestore

from .adapters.code_repositories import GitRepositoryReader
from .adapters.llm import LlmProfileInferer
from .adapters.notifications import TelegramNotifier, TelegramProfileReporter
from .adapters.persistence import FirestoreProfileStore
from .adapters.persistence.firestore_offer_messages import FirestoreOfferMessageIndex
from .adapters.resume import FileResumeSource
from .application import BuildProfessionalProfile, EnsureProfile
from .bootstrap import build_llm
from .config import Config, load_config, load_dotenv
from .domain.models import RepoRef
from .entrypoints.http import create_app
from .entrypoints.telegram import add_telegram_webhook
from .scoring.enrichment import JevOfferEnricher
from .scoring.firestore import FirestoreScoringStore
from .scoring.jev import JevScoringTool
from .scoring.run import ResendPendingNotifications, ScorePendingJobs
from .scoring.telegram import TelegramOfferNotifier


def build_webhook_app() -> FastAPI:
    load_dotenv()
    api_key = os.environ.get("JOB_AGENT_WEBHOOK_API_KEY", "")
    if not api_key.strip():
        raise ValueError("JOB_AGENT_WEBHOOK_API_KEY no está configurada")
    project = os.environ.get("FIRESTORE_PROJECT_ID", "").strip()
    if not project:
        raise ValueError("FIRESTORE_PROJECT_ID no está configurado")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not openrouter_key.strip():
        raise ValueError("OPENROUTER_API_KEY no está configurada")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not telegram_token.strip():
        raise ValueError("TELEGRAM_BOT_TOKEN no está configurado")
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not telegram_chat_id.strip():
        raise ValueError("TELEGRAM_CHAT_ID no está configurado")
    telegram_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not telegram_secret.strip():
        raise ValueError("TELEGRAM_WEBHOOK_SECRET no está configurado")

    cfg = load_config()
    threshold = cfg.matching.min_score_to_notify
    logging.getLogger("httpx").setLevel(logging.WARNING)
    client = firestore.Client(project=project)
    store = FirestoreScoringStore(client)
    offer_messages = FirestoreOfferMessageIndex(client)
    execution_lock = Lock()
    offer_notifier = TelegramOfferNotifier(telegram_token, telegram_chat_id)
    runner = ScorePendingJobs(
        store,
        store,
        JevScoringTool(openrouter_key),
        JevOfferEnricher(openrouter_key),
        offer_notifier,
        threshold,
        offer_messages,
    )
    app = create_app(runner, api_key, execution_lock=execution_lock)
    telegram = TelegramNotifier(telegram_token, telegram_chat_id)
    add_telegram_webhook(
        app,
        secret=telegram_secret,
        chat_id=telegram_chat_id.strip(),
        messenger=telegram,
        build_profile=build_profile_use_case(cfg, FirestoreProfileStore(client), TelegramProfileReporter(telegram)),
        resend_pending=ResendPendingNotifications(store, offer_notifier, offer_messages),
        execution_lock=execution_lock,
    )
    return app


def build_profile_use_case(
    cfg: Config, store: FirestoreProfileStore, reporter: TelegramProfileReporter
) -> BuildProfessionalProfile:
    ensure_profile = EnsureProfile(
        resume=FileResumeSource(cfg.resume_file),
        repositories=GitRepositoryReader(
            repositories=[RepoRef(url=r.url, branch=r.branch) for r in cfg.repositories],
            github_user=cfg.github_user,
            include_forks=cfg.include_forks,
            max_repos=cfg.max_repos,
        ),
        inferer=LlmProfileInferer(build_llm(cfg.llm, "profile")),
        store=store,
        refresh_days=cfg.profile_refresh_days,
        preferred_locations=cfg.search.locations,
    )
    return BuildProfessionalProfile(ensure_profile, store, reporter)


app = build_webhook_app()
