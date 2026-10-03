"""Plain-text Telegram notifications."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def send_text(self, text: str) -> None:
        resp = httpx.post(
            self._api("sendMessage"),
            data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"},
            timeout=30,
        )
        if resp.status_code != 200:
            log.error("Telegram sendMessage failed: %s %s", resp.status_code, resp.text[:300])
