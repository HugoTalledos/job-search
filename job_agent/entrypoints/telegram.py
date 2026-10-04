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
from ..application.manage_search_preferences import ManageSearchPreferences
from ..application.preference_models import DraftResolution, PreferencesView, Proposal
from ..scoring.models import ADJUST_CV_CALLBACK
from ..scoring.ports import OfferMessageIndex

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
BUILD_PROFILE_COMMANDS = {"/build-profile", "/build_profile"}
RESEND_PENDING_COMMAND = "/resend_pending"
ADJUST_CV_COMMAND = "/ajustar_cv"
PREFERENCES_COMMAND = "/preferencias"

BUILDING = "Voy a construir tu nuevo perfil profesional"
ALREADY_BUILDING = "Ya estoy construyendo tu perfil profesional; te aviso cuando termine."
RESENDING_PENDING = "Estoy buscando propuestas que hayan quedado pendientes de notificar"
ALREADY_RESENDING = "Ya estoy reenviando las propuestas pendientes."
HELP = ("Comandos disponibles:\n/build_profile — construir tu perfil profesional"
        "\n/resend_pending — reintentar las notificaciones pendientes"
        "\n📄 Ajustar CV — botón bajo cada oferta para recibir un CV ajustado en PDF"
        "\n/preferencias — ver o cambiar el tipo de ofertas que busco")
CV_ACCEPTED = "Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar."
CV_IN_PROGRESS = "Ya estoy ajustando tu CV para esta propuesta; te enviaré el PDF al terminar."
CV_NEEDS_REPLY = ("Para ajustar tu CV, pulsa «📄 Ajustar CV» bajo la oferta que te interesa. En ofertas "
                  "antiguas sin botón, responde a su mensaje con /ajustar_cv.")
CV_UNKNOWN_OFFER = ("No pude identificar la oferta de ese mensaje. Usa el botón «📄 Ajustar CV» de un "
                    "mensaje de oferta enviado por el bot.")
CV_NOT_STARTED = ("No pude iniciar el ajuste de tu CV. Revisa que existan tu CV base, tu perfil profesional "
                  "y la descripción completa de la oferta.")
PREFERENCES_REVIEWING = "Revisando tus preferencias…"
PREFERENCES_FAILED = "No pude revisar tus preferencias. Inténtalo de nuevo más tarde."
PREFERENCE_CALLBACK = re.compile(r"pref:(apply|cancel):([0-9a-f]{32})")
CALLBACK_INVALID = "Acción no válida"
CALLBACK_FAILED = "No pude completar la acción"
PLAN_REBUILD_FAILED = "Tu perfil se guardó, pero no pude actualizar el plan de búsqueda."
PLAN_NO_KEYWORDS = "Tu perfil se guardó, pero no hay palabras clave para buscar; agrega alguna con /preferencias."
CV_FAILED = ("No pude generar o enviar tu CV para esta propuesta. Puedes intentarlo de nuevo con el botón "
             "«📄 Ajustar CV» de la oferta.")
# Short ``answerCallbackQuery`` texts for the CV button; the chat message carries the details.
CV_BUTTON_ACCEPTED = "Ajustando tu CV…"
CV_BUTTON_IN_PROGRESS = "Ya estoy ajustando este CV"
CV_BUTTON_NOT_STARTED = "No pude iniciar el ajuste"


class ChatMessenger(Protocol):
    def send_text(self, text: str) -> None: ...


class ProfileBuilder(Protocol):
    def execute(self) -> Any: ...


class PreferencesChat(Protocol):
    def show(self, view: PreferencesView) -> None: ...

    def proposal(self, proposal: Proposal) -> None: ...

    def resolved(self, message_id: int, resolution: DraftResolution) -> None: ...

    def answer(self, callback_id: str, text: str) -> None: ...


def _callback_status(resolution: DraftResolution) -> str:
    """Short text for ``answerCallbackQuery``; the edited message carries the details."""
    if resolution.status == "applied":
        return "Preferencias guardadas" if resolution.plan is None else "Aplicado"
    if resolution.status == "cancelled":
        return "Cancelado"
    if resolution.status == "already_resolved":
        return "Esta propuesta ya estaba resuelta"
    return "Esta propuesta ya no es válida"


def _is_private_from(message: Any, sender: Any, chat_id: str) -> bool:
    """The private chat with the configured user, sent by that user."""
    if not isinstance(message, dict):
        return False
    chat = message.get("chat")
    return (isinstance(chat, dict) and str(chat.get("id")) == chat_id and chat.get("type") == "private"
            and isinstance(sender, dict) and str(sender.get("id")) == chat_id)


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
    preferences: ManageSearchPreferences,
    preferences_chat: PreferencesChat,
    execution_lock: LockType | None = None,
    bot_id: int | None = None,
) -> None:
    """Register ``POST /webhooks/telegram``.

    Only calls carrying ``secret`` (Telegram sends the one given to ``setWebhook``) are accepted, and only
    messages from ``chat_id`` are acted upon; anything else gets ``200`` so Telegram does not resend it.
    A tailored CV is requested with the «Ajustar CV» button under an offer message, or with ``/ajustar_cv``
    as a reply to an offer message sent by the bot (``bot_id``, when known) for offers sent without the
    button; both only in the private chat with that user. ``/preferencias`` and its inline buttons
    (``callback_query``) are likewise restricted to the private chat with that user.
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
        else:
            rebuild_plan()
        finally:
            build_lock.release()

    def rebuild_plan() -> None:
        try:
            result = preferences.rebuild_plan()
        except Exception as exc:
            log.error("No se pudo recompilar el plan de búsqueda (%s)", type(exc).__name__)
            notify(PLAN_REBUILD_FAILED)
            return
        if result.status == "no_keywords":
            notify(PLAN_NO_KEYWORDS)

    def notify(text: str) -> None:
        try:
            messenger.send_text(text)
        except Exception as exc:
            log.error("No se pudo enviar el aviso por Telegram (%s)", type(exc).__name__)

    # --- /preferencias and its inline buttons ---

    def run_proposal(request: str) -> None:
        try:
            preferences_chat.proposal(preferences.propose(request, chat_id))
        except Exception as exc:
            # Never log the candidate's text or the LLM answer.
            log.error("No se pudo revisar el cambio de preferencias (%s)", type(exc).__name__)
            notify(PREFERENCES_FAILED)

    def handle_preferences(message: dict, text: str, background_tasks: BackgroundTasks) -> None:
        if not _is_private_from(message, message.get("from"), chat_id):
            log.warning("Ignoring /preferencias outside the configured private chat")
            return
        parts = text.strip().split(maxsplit=1)
        request = parts[1].strip() if len(parts) > 1 else ""
        if not request:
            try:
                preferences_chat.show(preferences.show())
            except Exception as exc:
                log.error("No se pudieron mostrar las preferencias (%s)", type(exc).__name__)
                notify(PREFERENCES_FAILED)
            return
        try:
            messenger.send_text(PREFERENCES_REVIEWING)
        finally:
            background_tasks.add_task(run_proposal, request)

    def answer(callback_id: str, text: str) -> None:
        try:
            preferences_chat.answer(callback_id, text)
        except Exception as exc:
            log.error("No se pudo responder al botón de Telegram (%s)", type(exc).__name__)

    def handle_callback(query: dict, background_tasks: BackgroundTasks) -> None:
        callback_id = query.get("id")
        callback_id = callback_id if isinstance(callback_id, str) and callback_id else None
        message = query.get("message")
        data = query.get("data")
        if data == ADJUST_CV_CALLBACK:
            handle_cv_button(callback_id, message, query.get("from"), background_tasks)
            return
        match = PREFERENCE_CALLBACK.fullmatch(data) if isinstance(data, str) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        if (callback_id is None or match is None or type(message_id) is not int
                or not _is_private_from(message, query.get("from"), chat_id)):
            log.warning("Ignoring an invalid Telegram button press")
            if callback_id is not None:
                answer(callback_id, CALLBACK_INVALID)
            return
        action, draft_id = match.group(1), match.group(2)
        try:
            resolution = preferences.resolve(draft_id, chat_id, action)
        except Exception as exc:
            log.error("No se pudo resolver la propuesta %s (%s)", draft_id, type(exc).__name__)
            answer(callback_id, CALLBACK_FAILED)
            return
        answer(callback_id, _callback_status(resolution))
        try:
            preferences_chat.resolved(message_id, resolution)
        except Exception as exc:
            log.error("No se pudo editar el mensaje de la propuesta %s (%s)", draft_id, type(exc).__name__)

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

    def offer_posting_id(offer: dict) -> str | None:
        message_id = offer.get("message_id")
        if type(message_id) is not int:
            return None
        posting_id = offer_messages.resolve(chat_id, message_id)
        if posting_id:
            return posting_id
        urls = _text_link_urls(offer)  # offers sent before message tracking existed
        return offer_messages.resolve_unique_url(urls.pop()) if len(urls) == 1 else None

    def sent_by_this_bot(message: dict) -> bool:
        author = message.get("from")
        if not isinstance(author, dict) or author.get("is_bot") is not True:
            return False
        return bot_id is None or author.get("id") == bot_id

    def start_cv(offer: dict, background_tasks: BackgroundTasks) -> str:
        """Claim and schedule the CV for an offer message; returns a short status for button presses."""
        try:
            posting_id = offer_posting_id(offer)
            prepared = None if posting_id is None else cv_generator.prepare(posting_id, datetime.now(timezone.utc))
        except Exception as exc:
            log.error("No se pudo preparar el CV solicitado (%s)", type(exc).__name__)
            messenger.send_text(CV_NOT_STARTED)
            return CV_BUTTON_NOT_STARTED
        if prepared is None:
            messenger.send_text(CV_UNKNOWN_OFFER)
            return CV_BUTTON_NOT_STARTED
        if prepared.action == "in_progress":
            messenger.send_text(CV_IN_PROGRESS)
            return CV_BUTTON_IN_PROGRESS
        try:
            messenger.send_text(CV_ACCEPTED)
        finally:
            # The claim is already persisted: the work runs even if the acknowledgment fails.
            background_tasks.add_task(run_cv, prepared, offer["message_id"])
        return CV_BUTTON_ACCEPTED

    def handle_cv_button(callback_id: str | None, message: Any, sender: Any,
                         background_tasks: BackgroundTasks) -> None:
        # Telegram only routes presses of this bot's own buttons, so the offer message is the bot's.
        if (callback_id is None or not isinstance(message, dict)
                or type(message.get("message_id")) is not int or not _is_private_from(message, sender, chat_id)):
            log.warning("Ignoring an invalid CV button press")
            if callback_id is not None:
                answer(callback_id, CALLBACK_INVALID)
            return
        status = CV_BUTTON_NOT_STARTED
        try:
            status = start_cv(message, background_tasks)
        finally:
            answer(callback_id, status)

    def handle_adjust_cv(message: dict, background_tasks: BackgroundTasks) -> None:
        """``/ajustar_cv`` as a reply, kept for offer messages sent before they carried the button."""
        sender = message.get("from")
        if (message["chat"].get("type") != "private" or not isinstance(sender, dict)
                or str(sender.get("id")) != chat_id):
            log.warning("Ignoring /ajustar_cv outside the configured private chat")
            return
        replied = message.get("reply_to_message")
        if not isinstance(replied, dict):
            messenger.send_text(CV_NEEDS_REPLY)
            return
        if not sent_by_this_bot(replied):
            messenger.send_text(CV_UNKNOWN_OFFER)
            return
        start_cv(replied, background_tasks)

    def is_repeated(update_id: Any) -> bool:
        if not isinstance(update_id, int):
            return False
        with updates_lock:
            if update_id in recent_updates:
                return True
            recent_updates.append(update_id)
            return False

    def handle(update: dict, background_tasks: BackgroundTasks) -> None:
        query = update.get("callback_query")
        if isinstance(query, dict):
            if not is_repeated(update.get("update_id")):
                handle_callback(query, background_tasks)
            return
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
        if _command(text) == PREFERENCES_COMMAND:
            handle_preferences(message, text, background_tasks)
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
