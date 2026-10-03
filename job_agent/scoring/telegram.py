"""Telegram adapter for scored offer recommendations."""

from __future__ import annotations

import html

import httpx

from job_contracts import JobPosting

from .models import PostingEnrichment, ScoreResult


def _escaped(value: str, budget: int) -> str:
    parts: list[str] = []
    length = 0
    for char in value:
        escaped = html.escape(char)
        if length + len(escaped) > budget:
            break
        parts.append(escaped)
        length += len(escaped)
    return "".join(parts)


def format_offer_message(job: JobPosting, result: ScoreResult, enrichment: PostingEnrichment) -> str:
    lines = [
        f"🎯 <b>{_escaped(job.title, 600)}</b> — {_escaped(job.company, 350)}",
        f"📍 {_escaped(job.location, 300)}",
        f"⭐ Afinidad: <b>{result.score}/100</b>",
    ]
    if enrichment.required_language:
        lines.append(f"🗣 Idioma requerido: {_escaped(enrichment.required_language, 300)}")
    if enrichment.salary_range:
        lines.append(f"💰 Salario: {_escaped(enrichment.salary_range, 300)}")
    if job.url:
        lines.append(f"🔗 {_escaped(job.url, 1200)}")
    return "\n".join(lines)


class TelegramOfferNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        if not token.strip() or not chat_id.strip():
            raise ValueError("TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID son obligatorios")
        self.token = token
        self.chat_id = chat_id
        self.client = client or httpx.Client(timeout=30)

    def notify(self, job: JobPosting, result: ScoreResult, enrichment: PostingEnrichment) -> None:
        response = self.client.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data={
                "chat_id": self.chat_id,
                "text": format_offer_message(job, result, enrichment),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Telegram send failed (HTTP {response.status_code})")
        if response.json().get("ok") is not True:
            raise RuntimeError("Telegram rejected message")
