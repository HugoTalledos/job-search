"""Notifier port that only logs - for dry runs and local development without Telegram."""

from __future__ import annotations

import logging

from ...domain.models import JobAlert
from .telegram_notifier import format_message

log = logging.getLogger(__name__)


class ConsoleNotifier:
    def notify(self, alert: JobAlert) -> None:
        attachment = alert.application.attachment if alert.application else None
        log.info("[notification]\n%s\n(attachment: %s)", format_message(alert), attachment)
