"""Telegram messages for viewing and changing search preferences, with inline confirmation buttons."""

from __future__ import annotations

import re
from html import escape

import httpx

from job_contracts import SearchPreferences

from ...application.preference_models import DraftResolution, PreferencesView, Proposal
from ...domain.preference_edits import LABELS
from .telegram_notifier import TELEGRAM_LIMIT

MIGRATION_HINT = "No hay preferencias guardadas. Ejecuta job_agent seed-search-preferences en tu Mac."
STALE_PLAN = "⚠️ El plan aún no refleja la versión actual de tus preferencias"
APPLIED_PLAN_KEPT = "✅ Preferencias guardadas. El plan se mantiene hasta que haya palabras clave."
CANCELLED = "❌ Cancelado"
NO_LONGER_VALID = "⌛ Esta propuesta ya no es válida; vuelve a escribir /preferencias con lo que quieres."
PREVIEW_QUERIES = 5


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _fit(lines: list[str]) -> str:
    """Join escaped lines within Telegram's limit, dropping whole lines so no tag or entity is cut."""
    text = "\n".join(lines)
    if _units(text) <= TELEGRAM_LIMIT:
        return text
    kept = list(lines)
    while kept and _units("\n".join([*kept, "…"])) > TELEGRAM_LIMIT:
        kept.pop()
    if kept:
        return "\n".join([*kept, "…"])
    # A single oversized line: cut it and drop any entity left incomplete.
    cut = lines[0].encode("utf-16-le")[: (TELEGRAM_LIMIT - 1) * 2].decode("utf-16-le", errors="ignore")
    return re.sub(r"&[a-zA-Z#0-9]*$", "", cut) + "…"


def _value(prefs: SearchPreferences, field: str) -> str:
    value = getattr(prefs, field)
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, list):
        return ", ".join(value) if value else "—"
    return str(value)


class TelegramPreferencesChat:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        self.token, self.chat_id, self.client = token, chat_id, client

    def _call(self, method: str, payload: dict) -> None:
        try:
            post = self.client.post if self.client is not None else httpx.post
            response = post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload, timeout=30)
            if response.status_code != 200:
                raise ValueError("Unsuccessful HTTP status")
            if response.json().get("ok") is not True:
                raise ValueError("Telegram did not confirm success")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            # Never expose the token-bearing URL or the echoed message.
            raise RuntimeError(f"Telegram {method} failed") from None

    def _send(self, lines: list[str], reply_markup: dict | None = None) -> None:
        payload = {"chat_id": self.chat_id, "text": _fit(lines), "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call("sendMessage", payload)

    def show(self, view: PreferencesView) -> None:
        prefs, plan = view.preferences, view.plan
        if prefs is None:
            self._send([MIGRATION_HINT])
            return
        lines = [f"<b>Tus preferencias de búsqueda</b> (versión {prefs.version})"]
        lines += [f"{escape(label)}: {escape(_value(prefs, field))}" for field, label in LABELS.items()]
        lines.append("")
        lines.append(f"{len(plan.search.queries) if plan else 0} búsquedas en el plan vigente")
        if plan is not None and plan.preferences_version != prefs.version:
            lines.append(STALE_PLAN)
        self._send(lines)

    def proposal(self, proposal: Proposal) -> None:
        if proposal.kind == "missing_preferences":
            self._send([MIGRATION_HINT])
            return
        if proposal.kind == "rejected" or proposal.draft is None:
            self._send(["No apliqué cambios:", *(f"• {escape(p)}" for p in proposal.problems)])
            return
        draft, plan = proposal.draft, proposal.plan_preview
        queries = plan.search.queries if plan is not None else []
        lines = ["Entendí estos cambios:", *(escape(line) for line in draft.diff), ""]
        lines.append(f"{len(queries)} búsquedas")
        for query in queries[:PREVIEW_QUERIES]:
            where = f" @ {escape(query.location)}" if query.location else ""
            lines.append(f"• {escape(query.keywords)}{where}")
        if proposal.total_queries > len(queries):
            lines.append(f"⚠️ Se recortaron búsquedas: el plan usa {len(queries)} de {proposal.total_queries} posibles.")
        if plan is None:
            lines.append("Sin palabras clave: el plan actual se mantendría.")
        if proposal.problems:
            lines += ["", "No entendí:", *(f"• {escape(p)}" for p in proposal.problems)]
        draft_id = draft.draft_id
        self._send(lines, {"inline_keyboard": [[
            {"text": "✅ Aplicar", "callback_data": f"pref:apply:{draft_id}"},
            {"text": "❌ Cancelar", "callback_data": f"pref:cancel:{draft_id}"},
        ]]})

    def resolved(self, message_id: int, resolution: DraftResolution) -> None:
        self._call("editMessageText", {
            "chat_id": self.chat_id, "message_id": message_id, "text": _resolution_text(resolution),
            "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []},
        })

    def answer(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def _resolution_text(resolution: DraftResolution) -> str:
    if resolution.status == "applied":
        if resolution.plan is None:
            return APPLIED_PLAN_KEPT
        return f"✅ Aplicado: {len(resolution.plan.search.queries)} búsquedas"
    if resolution.status == "cancelled":
        return CANCELLED
    if resolution.status == "already_resolved":
        if resolution.previous_status == "APPLIED":
            return "✅ Aplicado"
        if resolution.previous_status == "CANCELLED":
            return CANCELLED
    return NO_LONGER_VALID
