"""Notifications. Telegram when configured; otherwise printed to the log (useful locally)."""

from __future__ import annotations

import html
import logging
import os
from pathlib import Path

import httpx

from .models import JobMatch, JobPosting, TailoredResume

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096


def format_message(job: JobPosting, match: JobMatch, tailored: TailoredResume | None, resume_link: str | None) -> str:
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
    if tailored:
        lines += ["", "<b>📝 Hoja de vida ajustada</b>", e(tailored.summary_for_candidate)]
        lines += [f"• <i>{e(c.section)}</i>: {e(c.change)}" for c in tailored.changes[:8]]
        if resume_link:
            lines.append(f'<a href="{e(resume_link, quote=True)}">Ver versión en el repositorio</a>')
    else:
        lines += ["", "Tu hoja de vida actual ya cubre bien esta oferta; no se modificó."]
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


class Notifier:
    def __init__(self) -> None:
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send(self, text: str, attachment: Path | None = None) -> None:
        if not self.enabled:
            log.info("[notification]\n%s\n(attachment: %s)", text, attachment)
            return
        resp = httpx.post(
            self._api("sendMessage"),
            data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"},
            timeout=30,
        )
        if resp.status_code != 200:
            log.error("Telegram sendMessage failed: %s %s", resp.status_code, resp.text[:300])
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
