"""Collect new LinkedIn postings for the remote agent to evaluate later."""

from __future__ import annotations

import logging

from ..domain.models import CollectionReport
from .ports import CollectorStore, JobCollectorSource

log = logging.getLogger(__name__)


class CollectJobs:
    def __init__(self, source: JobCollectorSource, store: CollectorStore) -> None:
        self.source = source
        self.store = store

    def execute(self) -> CollectionReport:
        plan = self.store.load_plan()
        batch = self.source.collect_new(plan.search, self.store.known_keys, plan.max_details_per_run)
        report = CollectionReport(
            leads=batch.leads, known=batch.known, fetched=len(batch.jobs),
            errors=[*batch.search_errors, *batch.detail_errors],
        )
        for job in batch.jobs:
            try:
                if self.store.save(job):
                    report.inserted += 1
            except Exception as exc:
                report.errors.append(f"save {job.external_id}: {exc}")
                log.exception("Could not save %s", job.external_id)
        log.info("Collector finished: %s", report.model_dump())
        return report
