"""ASGI composition for professional-profile affinity scoring."""

from __future__ import annotations

import os

from fastapi import FastAPI
from google.cloud import firestore

from .config import load_dotenv
from .entrypoints.http import create_app
from .scoring.firestore import FirestoreScoringStore
from .scoring.jev import JevScoringTool
from .scoring.run import ScorePendingJobs


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

    client = firestore.Client(project=project)
    store = FirestoreScoringStore(client)
    return create_app(ScorePendingJobs(store, store, JevScoringTool(openrouter_key)), api_key)


app = build_webhook_app()
