"""Telegram webhook: the bot's commands, answered right away and run in the background."""

from __future__ import annotations

import hmac
import logging
import re
from collections import deque
from threading import Lock
from typing import Any, Protocol

from fastapi import BackgroundTasks, FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
BUILD_PROFILE_COMMANDS = {"/build-profile", "/build_profile"}

BUILDING = "Voy a construir tu nuevo perfil profesional"
ALREADY_BUILDING = "Ya estoy construyendo tu perfil profesional; te aviso cuando termine."
HELP = "Comandos disponibles:\n/build_profile — construir tu perfil profesional"


class ChatMessenger(Protocol):
    def send_text(self, text: str) -> None: ...


class ProfileBuilder(Protocol):
    def execute(self) -> Any: ...


def _command(text: str) -> str:
    """``/Build_Profile@my_bot extra`` -> ``/build_profile``."""
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", 1)[0].lower()


def add_telegram_webhook(
    app: FastAPI,
    *,
    secret: str,
    chat_id: str,
    messenger: ChatMessenger,
    build_profile: ProfileBuilder,
) -> None:
    """Register ``POST /webhooks/telegram``.

    Only calls carrying ``secret`` (Telegram sends the one given to ``setWebhook``) are accepted, and only
    messages from ``chat_id`` are acted upon; anything else gets ``200`` so Telegram does not resend it.
    """
    if not secret.strip():
        raise ValueError("TELEGRAM_WEBHOOK_SECRET no está configurado")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise ValueError("TELEGRAM_WEBHOOK_SECRET solo admite 1 a 256 caracteres A-Z, a-z, 0-9, _ o -")
    expected_secret = secret.encode("utf-8")
    build_lock = Lock()
    recent_updates: deque[int] = deque(maxlen=100)
    updates_lock = Lock()

    def run_build() -> None:
        try:
            build_profile.execute()
        except Exception as exc:
            log.error("No se pudo construir el perfil profesional (%s)", type(exc).__name__)
        finally:
            build_lock.release()

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
        if _command(text) in BUILD_PROFILE_COMMANDS:
            if not build_lock.acquire(blocking=False):
                messenger.send_text(ALREADY_BUILDING)
                return
            try:
                messenger.send_text(BUILDING)
            finally:
                background_tasks.add_task(run_build)
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
