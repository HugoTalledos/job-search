"""Notifier port implemented with a Telegram bot (message + tailored resume as a document)."""

from __future__ import annotations

import html
import logging

import httpx

from ...domain.models import JobAlert

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096


def format_message(alert: JobAlert) -> str:
    job, match, tailored = alert.job, alert.match, alert.tailored
    resume_link = alert.application.link if alert.application else None
    e = html.escape
    lines = [
        f"<b>🎯 {e(job.title)}</b> — {e(job.company)}",
        f"📍 {e(job.location)} ({e(job.remote)}) · fuente: {e(job.source)}",
        f"⭐ Afinidad: <b>{match.score}/100</b> ({e(match.verdict)})",
        f'🔗 <a href="{e(job.url, quote=True)}">Ver publicación</a>' if job.url else "",
        "",
        "<b>Por qué encaja</b>",
        *[f"• {e(r)}" for r in match.reasons[:5]],
    ]
    if match.gaps:
        lines += ["", "<b>Brechas</b>", *[f"• {e(g)}" for g in match.gaps[:4]]]
    link = [f'<a href="{e(resume_link, quote=True)}">Ver versión en el repositorio</a>'] if resume_link else []
    source = alert.source_version
    source_desc = f"{e(source.job_title)} — {e(source.company)} ({source.created_at:%Y-%m-%d})" if source else ""
    if alert.resume_origin == "reused" and source:
        lines += ["", "<b>♻️ Hoja de vida reutilizada</b>", f"Usé la versión creada para {source_desc}; ya cubre esta oferta."]
        if alert.reuse_rationale:
            lines.append(f"<i>{e(alert.reuse_rationale)}</i>")
        lines += link
    elif tailored:
        title = "📝 Hoja de vida adaptada" if alert.resume_origin == "adapted" else "📝 Hoja de vida ajustada"
        lines += ["", f"<b>{title}</b>"]
        if alert.resume_origin == "adapted" and source:
            lines.append(f"Partí de la versión creada para {source_desc}.")
        lines.append(e(tailored.summary_for_candidate))
        lines += [f"• <i>{e(c.section)}</i>: {e(c.change)}" for c in tailored.changes[:8]]
        lines += link
    elif match.resume_undersells:
        lines += ["", "Tu hoja de vida podría reforzarse para esta oferta, pero no se generó una versión ajustada."]
    else:
        lines += ["", "Tu hoja de vida actual ya cubre bien esta oferta; no se modificó."]
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


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
