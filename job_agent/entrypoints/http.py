"""HTTP entrypoint for a future inference run."""

from __future__ import annotations

import hmac
import logging

from fastapi import FastAPI, Request, Response

from ..application.load_collected_jobs import LoadCollectedJobs

log = logging.getLogger(__name__)


def create_app(load_jobs: LoadCollectedJobs, api_key: str) -> FastAPI:
    if not api_key.strip():
        raise ValueError("JOB_AGENT_WEBHOOK_API_KEY no está configurada")

    app = FastAPI()

    @app.post("/webhooks/inference")
    def start_inference(request: Request) -> Response:
        supplied_key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied_key, api_key):
            return Response(status_code=401)

        try:
            load_jobs.execute()
        except Exception as exc:
            log.error("No se pudieron leer las ofertas de Firestore (%s)", type(exc).__name__)
            return Response(status_code=500)

        return Response(status_code=200)

    return app
