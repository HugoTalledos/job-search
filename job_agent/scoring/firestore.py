"""Read the stored profile and update collector postings in Firestore."""

from __future__ import annotations

import logging

from google.cloud import firestore
from pydantic import ValidationError

from job_contracts import JobPosting
from job_agent.domain.models import Profile

from .models import PendingNotification, PendingPosting, PostingEnrichment, ScoreResult

log = logging.getLogger(__name__)


class FirestoreScoringStore:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client

    def load(self) -> Profile:
        snapshot = self.client.collection("profiles").document("current").get()
        if not snapshot.exists:
            raise ValueError("No professional profile at profiles/current")
        return Profile.model_validate(snapshot.to_dict())

    def list_pending(self) -> list[PendingPosting]:
        pending: list[PendingPosting] = []
        for snapshot in self.client.collection("job_postings").stream():
            data = snapshot.to_dict()
            status = data.get("status", "PENDING") if isinstance(data, dict) else None
            if status == "EVALUATED":
                continue
            if status != "PENDING":
                log.warning("Unknown status on posting %s", snapshot.id)
                continue
            try:
                job = JobPosting.model_validate(data["job"])
            except (KeyError, TypeError, ValidationError):
                log.warning("Invalid posting document %s", snapshot.id)
                continue
            enrichment = None
            if data.get("enriched_at") is not None:
                language = data.get("required_language")
                salary = data.get("salary_range")
                if not all(value is None or isinstance(value, str) for value in (language, salary)):
                    log.warning("Invalid enrichment on posting %s", snapshot.id)
                    continue
                enrichment = PostingEnrichment(language, salary)
            pending.append(PendingPosting(document_id=snapshot.id, job=job, enrichment=enrichment))
        return pending

    def list_pending_notifications(self) -> list[PendingNotification]:
        pending: list[PendingNotification] = []
        for snapshot in self.client.collection("job_postings").stream():
            data = snapshot.to_dict()
            if not isinstance(data, dict) or data.get("status") != "PENDING_NOTIFICATION":
                continue
            try:
                job = JobPosting.model_validate(data["job"])
                score, confidence, model = data["score"], data["confidence"], data["score_model"]
                language, salary = data.get("required_language"), data.get("salary_range")
                if (type(score) is not int or type(confidence) not in (int, float)
                        or not isinstance(model, str) or not model
                        or not all(value is None or isinstance(value, str) for value in (language, salary))):
                    raise ValueError("Invalid saved notification fields")
                pending.append(PendingNotification(
                    snapshot.id, job, ScoreResult(score, float(confidence), model),
                    PostingEnrichment(language, salary),
                ))
            except (KeyError, TypeError, ValueError, ValidationError):
                log.warning("Invalid pending notification document %s", snapshot.id)
        return pending

    def mark_evaluated(self, document_id: str, result: ScoreResult) -> None:
        self.mark_scored(document_id, result, notify=False)

    def mark_enriched(self, document_id: str, enrichment: PostingEnrichment) -> None:
        self.client.collection("job_postings").document(document_id).update({
            "required_language": enrichment.required_language,
            "salary_range": enrichment.salary_range,
            "enriched_at": firestore.SERVER_TIMESTAMP,
        })

    def mark_scored(self, document_id: str, result: ScoreResult, notify: bool) -> None:
        self.client.collection("job_postings").document(document_id).update({
            "status": "PENDING_NOTIFICATION" if notify else "EVALUATED",
            "score": result.score,
            "confidence": result.confidence,
            "score_model": result.model,
            "evaluated_at": firestore.SERVER_TIMESTAMP,
        })

    def mark_notified(self, document_id: str) -> None:
        self.client.collection("job_postings").document(document_id).update({
            "status": "NOTIFIED",
            "notified_at": firestore.SERVER_TIMESTAMP,
        })
