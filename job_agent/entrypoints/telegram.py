"""Telegram webhook: the bot's commands, answered right away and run in the background."""

from __future__ import annotations

import hmac
import logging
import re
from _thread import LockType
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from fastapi import BackgroundTasks, FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from ..application import GenerateTailoredCv
from ..application.cv_models import PreparedCvRequest
from ..scoring.ports import OfferMessageIndex

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
BUILD_PROFILE_COMMANDS = {"/build-profile", "/build_profile"}
RESEND_PENDING_COMMAND = "/resend_pending"
ADJUST_CV_COMMAND = "/ajustar_cv"

BUILDING = "Voy a construir tu nuevo perfil profesional"
ALREADY_BUILDING = "Ya estoy construyendo tu perfil profesional; te aviso cuando termine."
RESENDING_PENDING = "Estoy buscando propuestas que hayan quedado pendientes de notificar"
ALREADY_RESENDING = "Ya estoy reenviando las propuestas pendientes."
HELP = ("Comandos disponibles:\n/build_profile — construir tu perfil profesional"
        "\n/resend_pending — reintentar las notificaciones pendientes"
        "\n/ajustar_cv — responde con él a una oferta para recibir un CV ajustado")
CV_ACCEPTED = "Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar."
CV_IN_PROGRESS = "Ya estoy ajustando tu CV para esta propuesta; te enviaré el PDF al terminar."
CV_NEEDS_REPLY = "Para ajustar tu CV, responde con /ajustar_cv al mensaje de la oferta que te interesa."
CV_UNKNOWN_OFFER = ("No pude identificar la oferta de ese mensaje. Responde con /ajustar_cv directamente "
                    "a un mensaje de oferta enviado por el bot.")
CV_NOT_STARTED = ("No pude iniciar el ajuste de tu CV. Revisa que existan tu CV base, tu perfil profesional "
                  "y la descripción completa de la oferta.")
CV_FAILED = ("No pude generar o enviar tu CV para esta propuesta. Puedes intentarlo de nuevo respondiendo "
             "/ajustar_cv a la oferta.")


class ChatMessenger(Protocol):
    def send_text(self, text: str) -> None: ...


class ProfileBuilder(Protocol):
    def execute(self) -> Any: ...


def _command(text: str) -> str:
    """``/Build_Profile@my_bot extra`` -> ``/build_profile``."""
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", 1)[0].lower()


def _text_link_urls(message: dict) -> set[str]:
    """Links Telegram attached to the bot's own formatting; plain ``url`` entities and raw text are ignored."""
    entities = message.get("entities")
    if not isinstance(entities, list):
        return set()
    return {
        entity["url"] for entity in entities
        if isinstance(entity, dict) and entity.get("type") == "text_link"
        and isinstance(entity.get("url"), str) and entity["url"].strip()
    }


def add_telegram_webhook(
    app: FastAPI,
    *,
    secret: str,
    chat_id: str,
    messenger: ChatMessenger,
    build_profile: ProfileBuilder,
    resend_pending: ProfileBuilder,
    cv_generator: GenerateTailoredCv,
    offer_messages: OfferMessageIndex,
    execution_lock: LockType | None = None,
    bot_id: int | None = None,
) -> None:
    """Register ``POST /webhooks/telegram``.

    Only calls carrying ``secret`` (Telegram sends the one given to ``setWebhook``) are accepted, and only
    messages from ``chat_id`` are acted upon; anything else gets ``200`` so Telegram does not resend it.
    ``/ajustar_cv`` is further restricted to the private chat with that user, as a reply to an offer
    message sent by the bot (``bot_id``, when known).
    """
    if not secret.strip():
        raise ValueError("TELEGRAM_WEBHOOK_SECRET no está configurado")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise ValueError("TELEGRAM_WEBHOOK_SECRET solo admite 1 a 256 caracteres A-Z, a-z, 0-9, _ o -")
    expected_secret = secret.encode("utf-8")
    build_lock = Lock()
    resend_lock = Lock()
    shared_lock = execution_lock or Lock()
    recent_updates: deque[int] = deque(maxlen=100)
    updates_lock = Lock()

    def run_build() -> None:
        try:
            build_profile.execute()
        except Exception as exc:
            log.error("No se pudo construir el perfil profesional (%s)", type(exc).__name__)
        finally:
            build_lock.release()

    def run_resend() -> None:
        try:
            with shared_lock:
                report = resend_pending.execute()
            if report.pending == 0:
                messenger.send_text("No hay propuestas pendientes de notificar.")
            else:
                messenger.send_text(f"Reenvío terminado: {report.notified} notificadas, {report.failed} fallidas.")
        except Exception as exc:
            log.error("No se pudieron reenviar las propuestas pendientes (%s)", type(exc).__name__)
            messenger.send_text("No pude revisar las propuestas pendientes. Revisa los registros del servicio.")
        finally:
            resend_lock.release()

    def run_cv(prepared: PreparedCvRequest, offer_message_id: int) -> None:
        try:
            cv_generator.execute(prepared, chat_id, offer_message_id)
        except Exception as exc:
            # The use case has already persisted a retryable state; never log CV or offer contents.
            log.error("No se pudo generar o enviar el CV solicitado (%s)", type(exc).__name__)
            try:
                messenger.send_text(CV_FAILED)
            except Exception as notify_exc:
                log.error("No se pudo avisar del fallo del CV (%s)", type(notify_exc).__name__)

    def offer_posting_id(replied: dict) -> str | None:
        author = replied.get("from")
        if not isinstance(author, dict) or author.get("is_bot") is not True:
            return None
        if bot_id is not None and author.get("id") != bot_id:
            return None
        message_id = replied.get("message_id")
        if type(message_id) is not int:
            return None
        posting_id = offer_messages.resolve(chat_id, message_id)
        if posting_id:
            return posting_id
        urls = _text_link_urls(replied)  # offers sent before message tracking existed
        return offer_messages.resolve_unique_url(urls.pop()) if len(urls) == 1 else None

    def handle_adjust_cv(message: dict, background_tasks: BackgroundTasks) -> None:
        sender = message.get("from")
        if (message["chat"].get("type") != "private" or not isinstance(sender, dict)
                or str(sender.get("id")) != chat_id):
            log.warning("Ignoring /ajustar_cv outside the configured private chat")
            return
        replied = message.get("reply_to_message")
        if not isinstance(replied, dict):
            messenger.send_text(CV_NEEDS_REPLY)
            return
        try:
            posting_id = offer_posting_id(replied)
            prepared = None if posting_id is None else cv_generator.prepare(posting_id, datetime.now(timezone.utc))
        except Exception as exc:
            log.error("No se pudo preparar el CV solicitado (%s)", type(exc).__name__)
            messenger.send_text(CV_NOT_STARTED)
            return
        if prepared is None:
            messenger.send_text(CV_UNKNOWN_OFFER)
            return
        if prepared.action == "in_progress":
            messenger.send_text(CV_IN_PROGRESS)
            return
        try:
            messenger.send_text(CV_ACCEPTED)
        finally:
            # The claim is already persisted: the work runs even if the acknowledgment fails.
            background_tasks.add_task(run_cv, prepared, replied["message_id"])

    def is_repeated(update_id: Any) -> bool:
        if not isinstance(update_id, int):
            return False
        with updates_lock:
            if update_id in recent_updates:
                return True
            recent_updates.append(update_id)
            return False

    def handle(update: dict, background_tasks: BackgroundTasks) -> None:
        message = update.get("message")
        if not isinstance(message, dict) or is_repeated(update.get("update_id")):
            return
        chat = message.get("chat")
        if not isinstance(chat, dict) or str(chat.get("id")) != chat_id:
            log.warning("Ignoring Telegram message from an unknown chat")
            return
        text = message.get("text")
        if not isinstance(text, str):
            return
        if _command(text) == ADJUST_CV_COMMAND:
            handle_adjust_cv(message, background_tasks)
            return
        if _command(text) in BUILD_PROFILE_COMMANDS:
            if not build_lock.acquire(blocking=False):
                messenger.send_text(ALREADY_BUILDING)
                return
            try:
                messenger.send_text(BUILDING)
            finally:
                background_tasks.add_task(run_build)
            return
        if _command(text) == RESEND_PENDING_COMMAND:
            if not resend_lock.acquire(blocking=False):
                messenger.send_text(ALREADY_RESENDING)
                return
            try:
                messenger.send_text(RESENDING_PENDING)
            finally:
                background_tasks.add_task(run_resend)
            return
        messenger.send_text(HELP)

    @app.post("/webhooks/telegram")
    async def telegram_update(request: Request, background_tasks: BackgroundTasks) -> Response:
        supplied = request.headers.get(SECRET_HEADER, "").encode("latin-1")
        if not hmac.compare_digest(supplied, expected_secret):
            return Response(status_code=401)
        try:
            update = await request.json()
        except ValueError:
            return Response(status_code=200)
        if isinstance(update, dict):
            try:
                await run_in_threadpool(handle, update, background_tasks)
            except Exception as exc:
                log.error("No se pudo atender el mensaje de Telegram (%s)", type(exc).__name__)
        return Response(status_code=200, background=background_tasks)
