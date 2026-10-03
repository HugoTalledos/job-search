"""External capabilities needed by the scoring workflow."""

from typing import Protocol

from job_contracts import JobPosting

from job_agent.domain.models import Profile

from .models import PendingNotification, PendingPosting, PostingEnrichment, ScoreResult


class ProfileReader(Protocol):
    def load(self) -> Profile: ...


class PendingPostingStore(Protocol):
    def list_pending(self) -> list[PendingPosting]: ...

    def mark_evaluated(self, document_id: str, result: ScoreResult) -> None: ...

    def mark_enriched(self, document_id: str, enrichment: PostingEnrichment) -> None: ...

    def mark_scored(self, document_id: str, result: ScoreResult, notify: bool) -> None: ...

    def mark_notified(self, document_id: str) -> None: ...


class PendingNotificationStore(Protocol):
    def list_pending_notifications(self) -> list[PendingNotification]: ...

    def mark_notified(self, document_id: str) -> None: ...


class ScoringTool(Protocol):
    def score(self, profile: Profile, job: JobPosting) -> ScoreResult: ...


class OfferEnricher(Protocol):
    def enrich(self, job: JobPosting) -> PostingEnrichment: ...


class OfferNotifier(Protocol):
    def notify(self, job: JobPosting, result: ScoreResult, enrichment: PostingEnrichment) -> None: ...
