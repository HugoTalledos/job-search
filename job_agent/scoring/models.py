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
