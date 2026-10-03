"""Correlate Telegram offer messages with the stored posting documents."""

from __future__ import annotations

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from job_agent.scoring.models import TelegramMessageRef


class FirestoreOfferMessageIndex:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client

    def record(self, ref: TelegramMessageRef, posting_id: str) -> None:
        self.client.collection("telegram_offer_messages").document(
            f"{ref.chat_id}_{ref.message_id}"
        ).set({"posting_id": posting_id, "sent_at": firestore.SERVER_TIMESTAMP})

    def resolve(self, chat_id: str, message_id: int) -> str | None:
        snapshot = self.client.collection("telegram_offer_messages").document(
            f"{chat_id}_{message_id}"
        ).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        posting_id = data.get("posting_id") if isinstance(data, dict) else None
        return posting_id if isinstance(posting_id, str) and posting_id else None

    def resolve_unique_url(self, url: str) -> str | None:
        if not url.strip():
            return None
        matches = list(self.client.collection("job_postings").where(
            filter=FieldFilter("job.url", "==", url)
        ).limit(2).stream())
        return matches[0].id if len(matches) == 1 else None
