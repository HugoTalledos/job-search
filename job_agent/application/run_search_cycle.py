"""Use case: one search cycle - search every source, score new postings, tailor, notify, remember."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from ..domain.models import CycleReport, JobAlert, JobPosting, MatchRecord
from ..domain.policies import MatchingPolicy, SearchPreferences, job_key
from .build_profile import EnsureProfile
from .ports import (
    ApplicationStore,
    JobMatcher,
    JobSource,
    MatchHistory,
    Notifier,
    ResumeSource,
    ResumeTailor,
    SeenJobsRepository,
)

log = logging.getLogger(__name__)


class RunSearchCycle:
    def __init__(
        self,
        *,
        ensure_profile: EnsureProfile,
        resume: ResumeSource,
        sources: Sequence[JobSource],
        matcher: JobMatcher,
        tailor: ResumeTailor,
        applications: ApplicationStore,
        notifier: Notifier,
        seen: SeenJobsRepository,
        history: MatchHistory,
        preferences: SearchPreferences,
        policy: MatchingPolicy,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.ensure_profile = ensure_profile
        self.resume = resume
        self.sources = sources
        self.matcher = matcher
        self.tailor = tailor
        self.applications = applications
        self.notifier = notifier
        self.seen = seen
        self.history = history
        self.preferences = preferences
        self.policy = policy
        self.clock = clock

    def execute(self) -> CycleReport:
        report = CycleReport()
        profile = self.ensure_profile.execute()
        resume_text = self.resume.read()
        criteria = self.preferences.criteria_for(profile, self.seen.seen_urls())

        postings = self._search_all(criteria, report)
        report.found = len(postings)
        fresh = self._new_postings(postings)[: self.preferences.max_jobs_per_run]
        report.new = len(fresh)
        log.info("%d postings found, %d new", report.found, report.new)

        for job in fresh:
            try:
                match = self.matcher.score(job, profile, resume_text)
            except Exception as exc:
                report.errors.append(f"score {job.title} @ {job.company}: {exc}")
                log.exception("Scoring failed for %s @ %s", job.title, job.company)
                continue
            report.scored += 1
            log.info("%3d  %s @ %s", match.score, job.title, job.company)

            alert = JobAlert(job=job, match=match)
            if self.policy.should_tailor(match):
                try:
                    alert.tailored = self.tailor.tailor(job, match, profile, resume_text)
                    alert.application = self.applications.save(job, match, alert.tailored)
                    report.tailored += 1
                except Exception as exc:
                    report.errors.append(f"tailor {job.title} @ {job.company}: {exc}")
                    log.exception("Tailoring failed for %s @ %s", job.title, job.company)
            if self.policy.should_notify(match):
                self.notifier.notify(alert)
                report.notified += 1

            self.seen.mark(job, match.score)
            self.history.append(
                MatchRecord(
                    at=self.clock(), key=job_key(job), title=job.title, company=job.company, url=job.url,
                    source=job.source, score=match.score, verdict=match.verdict, tailored=bool(alert.tailored),
                    resume_dir=alert.application.folder if alert.application else None,
                )
            )
            self.seen.commit()  # persist per posting so a crash mid-run never re-notifies

        self.seen.commit()
        log.info("Cycle done: %s", report.model_dump(exclude={"errors"}))
        return report

    def _search_all(self, criteria, report: CycleReport) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for source in self.sources:
            try:
                found = source.search(criteria)
                log.info("Source %s returned %d postings", source.name, len(found))
                postings += found
            except Exception as exc:  # one broken source must not stop the others
                report.errors.append(f"source {source.name}: {exc}")
                log.exception("Source %s failed", source.name)
        return postings

    def _new_postings(self, postings: list[JobPosting]) -> list[JobPosting]:
        fresh, keys = [], set()
        for job in postings:
            key = job_key(job)
            if key in keys or self.seen.is_seen(job) or self.preferences.is_excluded(job):
                continue
            keys.add(key)
            fresh.append(job)
        return fresh
