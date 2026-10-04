"""Collect new LinkedIn postings for the remote agent to evaluate later."""

from __future__ import annotations

import logging

from job_contracts.models import CollectionReport
from job_contracts.normalize import exclusion_reason
from .ports import CollectorStore, InferenceTrigger, JobCollectorSource

log = logging.getLogger(__name__)


class CollectJobs:
    def __init__(
        self, source: JobCollectorSource, store: CollectorStore, inference: InferenceTrigger | None = None,
    ) -> None:
        self.source = source
        self.store = store
        self.inference = inference

    def execute(self) -> CollectionReport:
        plan = self.store.load_plan()
        batch = self.source.collect_new(plan.search, self.store.known_keys, plan.max_details_per_run)
        report = CollectionReport(
            leads=batch.leads, known=batch.known, fetched=len(batch.jobs),
            errors=[*batch.search_errors, *batch.detail_errors],
        )
        for job in batch.jobs:
            if exclusion_reason(job, plan.exclude_companies, plan.exclude_title_keywords):
                report.excluded += 1
                continue
            try:
                if self.store.save(job):
                    report.inserted += 1
            except Exception as exc:
                report.errors.append(f"save {job.external_id}: {exc}")
                log.exception("Could not save %s", job.external_id)
        self._trigger_inference(report)
        log.info("Collector finished: %s", report.model_dump())
        return report

    def _trigger_inference(self, report: CollectionReport) -> None:
        """Wake the remote agent even without new postings, so earlier PENDING ones are retried."""
        if self.inference is None:
            return
        try:
            self.inference.trigger()
            report.inference_triggered = True
        except Exception as exc:
            report.errors.append(f"inference webhook: {exc}")
            log.error("Could not trigger the inference webhook: %s", exc)
