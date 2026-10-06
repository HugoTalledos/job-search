"""Telegram delivery of CV summaries and PDFs with confirmed message receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import json
import re

import httpx

from .telegram_notifier import TELEGRAM_LIMIT


def _bounded_text(text: str) -> str:
    # Plain text avoids cutting HTML tags/entities. Count UTF-16 units conservatively
    # for astral characters, and decode only complete Unicode characters.
    encoded = text.encode('utf-16-le')
    if len(encoded) <= TELEGRAM_LIMIT * 2:
        return text
    return encoded[:(TELEGRAM_LIMIT - 1) * 2].decode('utf-16-le', errors='ignore') + '…'


@dataclass(frozen=True)
class PreviewReceipt:
    preview_message_id: int
    markdown_message_id: int


def _callback(action: str, review_id: str, other_id: str | None = None) -> str:
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{16}', value) for value in [review_id, *([other_id] if other_id is not None else [])]):
        raise ValueError('Invalid review callback id')
    value = ':'.join(['cv', action, review_id, *([other_id] if other_id is not None else [])])
    if len(value.encode()) > 64:
        raise ValueError('Callback exceeds Telegram limit')
    return value


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

    def send_pdf(self, chat_id: str, reply_to_message_id: int, pdf: bytes, posting_id: str, *, review_id: str | None = None) -> int:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', posting_id):
            raise ValueError('Invalid posting id for Telegram button')
        buttons = [{'text': '✅ Apliqué', 'callback_data': f'applied:{posting_id}'}]
        if review_id:
            buttons.append({'text': '✏️ Corregir CV', 'callback_data': _callback('correct', review_id)})
        keyboard = json.dumps({'inline_keyboard': [buttons]}, ensure_ascii=False)
        return self._send('sendDocument', data={
            'chat_id': chat_id, 'reply_to_message_id': str(reply_to_message_id),
            'reply_markup': keyboard,
        }, files={'document': ('cv.pdf', pdf, 'application/pdf')}, timeout=60)


    def _text_document(self, chat_id, reply_to_message_id, name, text):
        return self._send('sendDocument', data={
            'chat_id': chat_id, 'reply_to_message_id': str(reply_to_message_id),
        }, files={'document': (name, text.encode('utf-8'), 'text/plain; charset=utf-8')}, timeout=60)

    def send_preview(self, chat_id: str, reply_to_message_id: int, review_id: str,
                     revision_id: str, summary: str, markdown: str) -> PreviewReceipt:
        keyboard = {'inline_keyboard': [
            [{'text': '✅ Aprobar y generar PDF', 'callback_data': _callback('approve', review_id, revision_id)}],
            [{'text': '✏️ Corregir', 'callback_data': _callback('edit', review_id)},
             {'text': '❌ Cancelar', 'callback_data': _callback('cancel', review_id)}]]}
        # The full text must be available before any approval button is published.
        document_id = self._text_document(chat_id, reply_to_message_id, 'cv-borrador.md', markdown)
        message_id = self._send('sendMessage', json={
            'chat_id': chat_id, 'reply_to_message_id': reply_to_message_id,
            'text': _bounded_text('Revisa el documento completo antes de aprobar. Responde a este mensaje para corregirlo.\n\n' + summary),
            'reply_markup': keyboard,
        }, timeout=30)
        return PreviewReceipt(message_id, document_id)

    def send_edit_proposal(self, chat_id: str, reply_to_message_id: int, review_id: str,
                           proposal_id: str, before: str, after: str, scope: Literal['global', 'local']) -> int:
        keyboard = {'inline_keyboard': [[
            {'text': '✅ Confirmar', 'callback_data': _callback('confirm', review_id, proposal_id)},
            {'text': '❌ Rechazar', 'callback_data': _callback('reject', review_id, proposal_id)}]]}
        if scope not in {'global', 'local'}:
            raise ValueError('Invalid correction scope')
        scope_text = ('El dato confirmado quedará vigente en tu perfil y futuros CV.' if scope == 'global'
                      else 'Solo cambia esta versión del CV.')
        diff = f'Antes:\n{before}\n\nDespués:\n{after}'
        text = scope_text + '\n\n' + diff
        if len(text.encode('utf-16-le')) > TELEGRAM_LIMIT * 2:
            self._text_document(chat_id, reply_to_message_id, 'correccion.txt', text)
            text = scope_text + '\nConsulta el cambio completo en correccion.txt.\n\n' + diff
        return self._send('sendMessage', json={
            'chat_id': chat_id, 'reply_to_message_id': reply_to_message_id,
            'text': _bounded_text(text), 'reply_markup': keyboard,
        }, timeout=30)
