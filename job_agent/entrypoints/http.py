"""HTTP entrypoint that starts affinity scoring in the background."""

from __future__ import annotations

import hmac
import logging
from threading import Lock

from fastapi import BackgroundTasks, FastAPI, Request, Response

from ..scoring.run import ScorePendingJobs

log = logging.getLogger(__name__)


def create_app(runner: ScorePendingJobs, api_key: str) -> FastAPI:
    if not api_key.strip():
        raise ValueError("JOB_AGENT_WEBHOOK_API_KEY no está configurada")
    expected_key = api_key.encode("utf-8")
    run_lock = Lock()

    app = FastAPI()

    def run_scoring() -> None:
        with run_lock:
            try:
                runner.execute()
            except Exception as exc:
                log.error("No se pudo completar la evaluación (%s)", type(exc).__name__)

    @app.post("/webhooks/inference")
    def start_inference(request: Request, background_tasks: BackgroundTasks) -> Response:
        supplied_key = request.headers.get("X-API-Key", "").encode("latin-1")
        if not hmac.compare_digest(supplied_key, expected_key):
            return Response(status_code=401)

        background_tasks.add_task(run_scoring)
        return Response(status_code=200, background=background_tasks)

    return app
