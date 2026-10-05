"""HTTP entrypoint that starts affinity scoring in the background."""

from __future__ import annotations

import hmac
import logging
from _thread import LockType
from threading import Lock
from typing import Literal, Protocol

from fastapi import BackgroundTasks, FastAPI, Request, Response
from pydantic import BaseModel, Field

from ..scoring.run import ScorePendingJobs, ScoreReport

log = logging.getLogger(__name__)


class StatusMessenger(Protocol):
    def send_text(self, text: str) -> None: ...


class CollectionStatus(BaseModel):
    status: Literal["started", "issues", "failed"]


class CollectionSummary(BaseModel):
    known_notified: int = Field(default=0, ge=0)
    known_other: int = Field(default=0, ge=0)
    excluded: int = Field(default=0, ge=0)


class InferenceRequest(BaseModel):
    collection: CollectionSummary | None = None


def create_app(runner: ScorePendingJobs, api_key: str, execution_lock: LockType | None = None,
               messenger: StatusMessenger | None = None) -> FastAPI:
    if not api_key.strip():
        raise ValueError("JOB_AGENT_WEBHOOK_API_KEY no está configurada")
    expected_key = api_key.encode("utf-8")
    run_lock = execution_lock or Lock()

    app = FastAPI()

    def notify(text: str) -> None:
        if messenger is None:
            return
        try:
            messenger.send_text(text)
        except Exception as exc:
            log.error("No se pudo enviar el estado de la evaluación (%s)", type(exc).__name__)

    def authorized(request: Request) -> bool:
        supplied_key = request.headers.get("X-API-Key", "").encode("latin-1")
        return hmac.compare_digest(supplied_key, expected_key)

    @app.post("/webhooks/collection-status")
    def collection_status(request: Request, status: CollectionStatus) -> Response:
        if not authorized(request):
            return Response(status_code=401)
        if status.status == "started":
            notify("Inicié una nueva búsqueda de ofertas.")
        elif status.status == "issues":
            notify("La búsqueda terminó con errores; algunos resultados pueden faltar. Revisa los registros del buscador.")
        else:
            notify("No pude completar la búsqueda de esta corrida. Revisa los registros del buscador.")
        return Response(status_code=200)

    def run_scoring(collection: CollectionSummary | None) -> None:
        with run_lock:
            notify("Inicié la evaluación de ofertas de esta corrida.")
            try:
                report = runner.execute()
            except Exception as exc:
                log.error("No se pudo completar la evaluación (%s)", type(exc).__name__)
                notify("No pude completar la evaluación de esta corrida. Revisa los registros del servicio.")
            else:
                if isinstance(report, ScoreReport):
                    if report.evaluated == report.failed == report.notified == 0 and (
                        collection is None or not any((collection.known_notified, collection.known_other, collection.excluded))
                    ):
                        notify("Evaluación terminada: no había ofertas pendientes y no se enviaron propuestas.")
                    else:
                        count = report.evaluated
                        offers = "oferta evaluada" if count == 1 else "ofertas evaluadas"
                        notified = (f"{report.notified} {'notificada' if report.notified == 1 else 'notificadas'}"
                                    if report.notified else "ninguna se notificó")
                        failures = (f", {report.failed} {'fallida' if report.failed == 1 else 'fallidas'}"
                                    if report.failed else "")
                        message = f"Evaluación terminada: {count} {offers}, {notified}{failures}."
                        if report.notified == 0:
                            reasons = []
                            if collection is not None:
                                if collection.known_notified:
                                    reasons.append(f"{collection.known_notified} ya se habían notificado")
                                if collection.known_other:
                                    reasons.append(f"{collection.known_other} ya estaban registradas")
                                if collection.excluded:
                                    verb = "se excluyó" if collection.excluded == 1 else "se excluyeron"
                                    reasons.append(f"{collection.excluded} {verb} por tus preferencias")
                            if report.below_threshold:
                                verb = "alcanzó" if report.below_threshold == 1 else "alcanzaron"
                                reason = f"{report.below_threshold} no {verb} el puntaje mínimo de afinidad con tu perfil"
                                if report.min_score_to_notify is not None:
                                    reason += f" ({report.min_score_to_notify}/100)"
                                reasons.append(reason)
                            if report.failed:
                                verb = "falló" if report.failed == 1 else "fallaron"
                                reasons.append(f"{report.failed} {verb} durante la evaluación o el envío")
                            if reasons:
                                message += f" Motivos: {', '.join(reasons)}."
                        notify(message)

    @app.post("/webhooks/inference")
    def start_inference(request: Request, background_tasks: BackgroundTasks,
                        payload: InferenceRequest | None = None) -> Response:
        if not authorized(request):
            return Response(status_code=401)

        background_tasks.add_task(run_scoring, payload.collection if payload is not None else None)
        return Response(status_code=200, background=background_tasks)

    return app
