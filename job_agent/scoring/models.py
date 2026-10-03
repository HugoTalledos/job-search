"""Values exchanged by the scoring workflow."""

from dataclasses import dataclass

from job_contracts import JobPosting


@dataclass(frozen=True)
class PendingPosting:
    document_id: str
    job: JobPosting


@dataclass(frozen=True)
class ScoreResult:
    score: int
    confidence: float
    model: str
