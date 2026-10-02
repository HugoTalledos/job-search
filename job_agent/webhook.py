"""ASGI composition for the future remote inference entrypoint."""

from __future__ import annotations

import os

from fastapi import FastAPI
from google.cloud import firestore

from .adapters.persistence.firestore_postings import FirestorePostingsRepository
from .application.load_collected_jobs import LoadCollectedJobs
from .config import load_dotenv
from .entrypoints.http import create_app


def build_webhook_app() -> FastAPI:
    load_dotenv()
    api_key = os.environ.get("JOB_AGENT_WEBHOOK_API_KEY", "")
    if not api_key.strip():
        raise ValueError("JOB_AGENT_WEBHOOK_API_KEY no está configurada")
    project = os.environ.get("FIRESTORE_PROJECT_ID", "").strip()
    if not project:
        raise ValueError("FIRESTORE_PROJECT_ID no está configurado")

    client = firestore.Client(project=project)
    return create_app(LoadCollectedJobs(FirestorePostingsRepository(client)), api_key)


app = build_webhook_app()
