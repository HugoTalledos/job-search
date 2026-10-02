"""Notifier port implemented with a Telegram bot (message + tailored resume as a document)."""

from __future__ import annotations

import html
import logging

import httpx

from ...domain.models import JobAlert

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096


def format_message(alert: JobAlert) -> str:
    job, match = alert.job, alert.match
    e = html.escape
    lines = [
        f"<b>🎯 {e(job.title)}</b> — {e(job.company)}",
        f"📍 {e(job.location)} ({e(job.remote)}) · fuente: {e(job.source)}",
        f"⭐ Afinidad: <b>{match.score}/100</b> ({e(match.verdict)})",
    ]
    if match.english_level.strip():
        lines.append(f"🗣 Inglés: {e(match.english_level)}")
    if match.salary_range.strip():
        lines.append(f"💰 Salario: {e(match.salary_range)}")
    if job.url:
        lines.append(f'🔗 <a href="{e(job.url, quote=True)}">Ver publicación</a>')
    lines += ["", _resume_line(alert)]
    if alert.application and not alert.resume_failed:
        if alert.application.link:
            lines.append(f'<a href="{e(alert.application.link, quote=True)}">Ver la hoja de vida</a>')
        else:
            lines.append(f"📁 <code>{e(alert.application.folder)}</code>")
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


def _resume_line(alert: JobAlert) -> str:
    """One line saying what happened with the resume: rewritten, reused, not generated, or unchanged."""
    source = alert.source_version
    based_on = f" (versión creada para {html.escape(source.job_title)} — {html.escape(source.company)})" if source else ""
    if alert.resume_failed:
        return "⚠️ <b>Hoja de vida:</b> no se pudo generar"
    if alert.resume_origin == "reused":
        return f"♻️ <b>Hoja de vida:</b> reutilizada{based_on}"
    if alert.resume_origin == "adapted":
        return f"📝 <b>Hoja de vida:</b> reescrita a partir de una existente{based_on}"
    if alert.resume_origin == "created":
        return "📝 <b>Hoja de vida:</b> reescrita para esta oferta"
    return "📄 <b>Hoja de vida:</b> sin cambios, se usa la original"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def notify(self, alert: JobAlert) -> None:
        self.send_text(format_message(alert))
        attachment = alert.application.attachment if alert.application else None
        if attachment and attachment.exists():
            with attachment.open("rb") as fh:
                resp = httpx.post(
                    self._api("sendDocument"),
                    data={"chat_id": self.chat_id},
                    files={"document": (attachment.name, fh)},
                    timeout=60,
                )
            if resp.status_code != 200:
                log.error("Telegram sendDocument failed: %s %s", resp.status_code, resp.text[:300])

    def send_text(self, text: str) -> None:
        resp = httpx.post(
            self._api("sendMessage"),
            data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"},
            timeout=30,
        )
        if resp.status_code != 200:
            log.error("Telegram sendMessage failed: %s %s", resp.status_code, resp.text[:300])
