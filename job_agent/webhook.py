"""ASGI composition: affinity scoring webhook and the Telegram bot that controls the agent."""

from __future__ import annotations

import os
import logging
from threading import Lock

from fastapi import FastAPI
from google.cloud import firestore

from job_contracts import SearchPreferences

from .adapters.code_repositories import GitRepositoryReader
from .adapters.llm import LlmJobMatcher, LlmProfileInferer, LlmResumeTailor
from .adapters.notifications import TelegramNotifier, TelegramProfileReporter
from .adapters.notifications.telegram_cv import TelegramCvDelivery
from .adapters.persistence import FirestoreProfileStore, FirestoreSearchSettingsStore
from .adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
from .adapters.persistence.firestore_cv_tracking import FirestoreCvTrackingStore
from .adapters.persistence.firestore_offer_messages import FirestoreOfferMessageIndex
from .adapters.resume import FileResumeSource
from .adapters.resume.pdf_renderer import RequiredPdfRenderer
from .application import BuildProfessionalProfile, EnsureProfile, GenerateTailoredCv
from .bootstrap import build_llm
from .config import Config, load_config, load_dotenv, require_firebase_storage_bucket
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
    storage_bucket = require_firebase_storage_bucket()

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
        build_profile=build_profile_use_case(
            cfg, FirestoreProfileStore(client), TelegramProfileReporter(telegram), FirestoreSearchSettingsStore(client)
        ),
        resend_pending=ResendPendingNotifications(store, offer_notifier, offer_messages),
        cv_generator=cv_generator_use_case(cfg, client, store, storage_bucket, telegram_token),
        offer_messages=offer_messages,
        execution_lock=execution_lock,
        bot_id=_bot_id(telegram_token),
    )
    return app


def _bot_id(token: str) -> int | None:
    """Telegram bot tokens start with the bot's user id (``123456:ABC...``)."""
    prefix = token.strip().split(":", 1)[0]
    return int(prefix) if prefix.isascii() and prefix.isdigit() else None


def cv_generator_use_case(
    cfg: Config, client: firestore.Client, profiles: FirestoreScoringStore, bucket: str, telegram_token: str
) -> GenerateTailoredCv:
    tracking = FirestoreCvTrackingStore(client)
    models: dict = {}
    return GenerateTailoredCv(
        resume=FileResumeSource(cfg.resume_file),
        profile_reader=profiles,
        posting_reader=tracking,
        matcher=LlmJobMatcher(build_llm(cfg.llm, "match", models)),
        tailor=LlmResumeTailor(build_llm(cfg.llm, "tailor", models)),
        renderer=RequiredPdfRenderer(),
        tracking=tracking,
        artifacts=FirebaseCvArtifactStore(bucket),
        delivery=TelegramCvDelivery(telegram_token),
    )


def build_profile_use_case(
    cfg: Config,
    store: FirestoreProfileStore,
    reporter: TelegramProfileReporter,
    settings: FirestoreSearchSettingsStore,
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
        preferred_locations=lambda: (settings.load_preferences() or SearchPreferences()).locations,
    )
    return BuildProfessionalProfile(ensure_profile, store, reporter)


app = build_webhook_app()
