"""Interfaces required by the collection use case."""

from collections.abc import Callable
from typing import Protocol

from job_contracts.models import CollectorPlan, JobLead, JobPosting, SearchPlan, SourceCollection


class JobCollectorSource(Protocol):
    def collect_new(
        self, plan: SearchPlan, known_keys: Callable[[list[JobLead]], set[str]], max_details: int,
    ) -> SourceCollection: ...


class CollectorStore(Protocol):
    def load_plan(self) -> CollectorPlan: ...
    def known_keys(self, leads: list[JobLead]) -> set[str]: ...
    def save(self, job: JobPosting) -> bool: ...


class InferenceTrigger(Protocol):
    def trigger(self) -> None: ...
