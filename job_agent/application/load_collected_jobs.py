"""Read the collector's offers without evaluating them yet."""

from __future__ import annotations

from ..domain.models import JobPosting
from .ports import CollectedPostingsRepository


class LoadCollectedJobs:
    def __init__(self, repository: CollectedPostingsRepository) -> None:
        self.repository = repository

    def execute(self) -> list[JobPosting]:
        return self.repository.list_postings()
