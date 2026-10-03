"""ASGI composition for professional-profile affinity scoring."""

from __future__ import annotations

import os
import logging

from fastapi import FastAPI
from google.cloud import firestore

from .config import load_config, load_dotenv
from .entrypoints.http import create_app
from .scoring.enrichment import JevOfferEnricher
from .scoring.firestore import FirestoreScoringStore
from .scoring.jev import JevScoringTool
from .scoring.run import ScorePendingJobs
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

    threshold = load_config().matching.min_score_to_notify
    logging.getLogger("httpx").setLevel(logging.WARNING)
    client = firestore.Client(project=project)
    store = FirestoreScoringStore(client)
    runner = ScorePendingJobs(
        store,
        store,
        JevScoringTool(openrouter_key),
        JevOfferEnricher(openrouter_key),
        TelegramOfferNotifier(telegram_token, telegram_chat_id),
        threshold,
    )
    return create_app(runner, api_key)


app = build_webhook_app()
