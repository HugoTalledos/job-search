"""Values exchanged by the scoring workflow."""

from dataclasses import dataclass

from job_contracts import JobPosting


@dataclass(frozen=True)
class PostingEnrichment:
    required_language: str | None = None
    salary_range: str | None = None


@dataclass(frozen=True)
class PendingPosting:
    document_id: str
    job: JobPosting
    enrichment: PostingEnrichment | None = None


@dataclass(frozen=True)
class ScoreResult:
    score: int
    confidence: float
    model: str


@dataclass(frozen=True)
class PendingNotification:
    document_id: str
    job: JobPosting
    result: ScoreResult
    enrichment: PostingEnrichment


# ``callback_data`` of the button under each offer message that asks for a tailored CV.
ADJUST_CV_CALLBACK = "cv:adjust"


@dataclass(frozen=True)
class TelegramMessageRef:
    chat_id: str
    message_id: int
