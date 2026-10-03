"""Telegram delivery of CV summaries and PDFs with confirmed message receipts."""

from __future__ import annotations

import httpx

from .telegram_notifier import TELEGRAM_LIMIT


def _bounded_text(text: str) -> str:
    # Plain text avoids cutting HTML tags/entities. Count UTF-16 units conservatively
    # for astral characters, and decode only complete Unicode characters.
    encoded = text.encode('utf-16-le')
    if len(encoded) <= TELEGRAM_LIMIT * 2:
        return text
    return encoded[:(TELEGRAM_LIMIT - 1) * 2].decode('utf-16-le', errors='ignore') + '…'


class TelegramCvDelivery:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self.token, self.client = token, client

    def _send(self, method: str, **kwargs) -> int:
        try:
            post = self.client.post if self.client is not None else httpx.post
            response = post(f'https://api.telegram.org/bot{self.token}/{method}', **kwargs)
            if response.status_code != 200:
                raise ValueError('Unsuccessful HTTP status')
            body = response.json()
            if body.get('ok') is not True:
                raise ValueError('Telegram did not confirm success')
            message_id = body['result']['message_id']
            if type(message_id) is not int or message_id <= 0:
                raise ValueError('Telegram did not return a message receipt')
            return message_id
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            # Do not expose a token-bearing request URL or Telegram's echoed text.
            raise RuntimeError(f'Telegram {method} failed') from None

    def send_summary(self, chat_id: str, reply_to_message_id: int, summary: str) -> int:
        return self._send('sendMessage', json={
            'chat_id': chat_id, 'reply_to_message_id': reply_to_message_id,
            'text': _bounded_text(summary), 'disable_web_page_preview': True,
        }, timeout=30)

    def send_pdf(self, chat_id: str, reply_to_message_id: int, pdf: bytes) -> int:
        return self._send('sendDocument', data={
            'chat_id': chat_id, 'reply_to_message_id': str(reply_to_message_id),
        }, files={'document': ('cv.pdf', pdf, 'application/pdf')}, timeout=60)
