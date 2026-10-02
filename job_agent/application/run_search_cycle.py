"""Use case: one search cycle.

Deterministic first, LLM last:
1. build the search plan from the profile and preferences;
2. each source runs the queries; ids already processed (or repeated in this run) are rejected before
   any detail is fetched;
3. fetched postings go through the deterministic filters (excluded companies and title words, work type,
   age, duplicates of processed postings under another id);
4. only the survivors are scored by the JobMatcher (the LLM decides fit), then tailored and notified.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from ..domain.models import CycleReport, JobAlert, JobLead, JobPosting, MatchRecord, Profile, SearchPlan
from ..domain.policies import (
    JobFilter,
    MatchingPolicy,
    ReusePolicy,
    SearchPreferences,
    duplicate_signature,
    job_key,
    lead_key,
    resume_fingerprint,
)
from .build_profile import EnsureProfile
from .ports import (
    ApplicationStore,
    JobMatcher,
    JobSource,
    MatchHistory,
    Notifier,
    ResumeSelector,
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
        selector: ResumeSelector,
        applications: ApplicationStore,
        notifier: Notifier,
        seen: SeenJobsRepository,
        history: MatchHistory,
        preferences: SearchPreferences,
        job_filter: JobFilter,
        policy: MatchingPolicy,
        reuse: ReusePolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.ensure_profile = ensure_profile
        self.resume = resume
        self.sources = sources
        self.matcher = matcher
        self.tailor = tailor
        self.selector = selector
        self.applications = applications
        self.notifier = notifier
        self.seen = seen
        self.history = history
        self.preferences = preferences
        self.job_filter = job_filter
        self.policy = policy
        self.reuse = reuse or ReusePolicy()
        self.clock = clock

    def execute(self, refresh_profile: bool = False) -> CycleReport:
        report = CycleReport()
        profile = self.ensure_profile.execute(force=refresh_profile)
        resume_text = self.resume.read()
        base_fp = resume_fingerprint(resume_text)
        plan = self.preferences.plan_for(profile)
        log.info("Search plan: %s", [f"{q.keywords} @ {q.location}" for q in plan.queries])

        postings = self._collect(plan, report)
        candidates = self._apply_filters(postings, report)
        report.candidates = len(candidates)
        fresh = candidates[: self.preferences.max_jobs_per_run]
        log.info(
            "%d ids found, %d already known, %d fetched, filtered %s, %d candidates (scoring %d)",
            report.leads, report.known_leads, report.fetched, report.filtered, report.candidates, len(fresh),
        )

        for job in fresh:
            try:
                match = self.matcher.score(job, profile, resume_text)
            except Exception as exc:
                report.errors.append(f"score {job.title} @ {job.company}: {exc}")
                log.exception("Scoring failed for %s @ %s", job.title, job.company)
                continue
            report.scored += 1
            job = self._fill_display_fields(job, match)
            log.info("%3d  %s @ %s", match.score, job.title, job.company)

            alert = JobAlert(job=job, match=match)
            if self.policy.should_tailor(match):
                try:
                    self._prepare_resume(alert, profile, resume_text, base_fp)
                    if alert.resume_origin == "created":
                        report.tailored += 1
                    elif alert.resume_origin == "adapted":
                        report.adapted += 1
                    elif alert.resume_origin == "reused":
                        report.reused += 1
                except Exception as exc:
                    report.errors.append(f"tailor {job.title} @ {job.company}: {exc}")
                    log.exception("Tailoring failed for %s @ %s", job.title, job.company)
            if self.policy.should_notify(match):
                self.notifier.notify(alert)
                report.notified += 1

            self.seen.mark(job, outcome="puntuada", score=match.score)
            self.history.append(
                MatchRecord(
                    at=self.clock(), key=job_key(job), title=job.title, company=job.company, url=job.url,
                    source=job.source, score=match.score, verdict=match.verdict, tailored=bool(alert.tailored),
                    resume_origin=alert.resume_origin,
                    resume_dir=alert.application.folder if alert.application else None,
                )
            )
            self.seen.commit()  # persist per posting so a crash mid-run never re-notifies

        self.seen.commit()
        log.info("Cycle done: %s", report.model_dump(exclude={"errors"}))
        return report

    def _prepare_resume(self, alert: JobAlert, profile: Profile, resume_text: str, base_fp: str) -> None:
        """Reuse a stored version that already fits, adapt the closest one, or create a new one."""
        job, match = alert.job, alert.match
        candidates = self.reuse.candidates(self.applications.list_versions(), base_fp)
        by_id = {v.id: v for v in candidates}
        decision = self.selector.choose(job, match, candidates) if candidates else None
        chosen = by_id.get(decision.version_id) if decision else None
        if decision and decision.action != "create" and chosen is None:
            log.warning("Selector chose unknown version %r; creating a new resume", decision.version_id)

        if decision and chosen and decision.action == "reuse":
            self.applications.record_use(chosen.id, job)
            alert.application = self.applications.locate(chosen.id)
            alert.resume_origin, alert.source_version = "reused", chosen
            alert.reuse_rationale = decision.rationale
            log.info("Reusing %s for %s @ %s", chosen.id, job.title, job.company)
            return

        if decision and chosen and decision.action == "adapt":
            starting_from = self.applications.load_markdown(chosen.id)
            alert.tailored = self.tailor.tailor(job, match, profile, resume_text, starting_from=starting_from)
            alert.application = self.applications.save(job, match, alert.tailored, base_fp, adapted_from=chosen.id)
            alert.resume_origin, alert.source_version = "adapted", chosen
            alert.reuse_rationale = decision.rationale
            return

        alert.tailored = self.tailor.tailor(job, match, profile, resume_text)
        alert.application = self.applications.save(job, match, alert.tailored, base_fp)
        alert.resume_origin = "created"

    def _collect(self, plan: SearchPlan, report: CycleReport) -> list[JobPosting]:
        run_keys: set[str] = set()

        def admit(lead: JobLead) -> bool:
            report.leads += 1
            key = lead_key(lead)
            if key in run_keys or self.seen.is_seen_key(key):
                report.known_leads += 1
                return False
            run_keys.add(key)
            return True

        postings: list[JobPosting] = []
        for source in self.sources:
            budget = self.preferences.max_details_per_run - len(postings)
            if budget <= 0:
                break
            try:
                found = source.collect(plan, admit, budget)
                log.info("Source %s returned %d new postings", source.name, len(found))
                postings += found
            except Exception as exc:  # one broken source must not stop the others
                report.errors.append(f"source {source.name}: {exc}")
                log.exception("Source %s failed", source.name)
        report.fetched = len(postings)
        return postings

    def _apply_filters(self, postings: list[JobPosting], report: CycleReport) -> list[JobPosting]:
        candidates, signatures, keys = [], set(), set()
        now = self.clock()
        for job in postings:
            reason = self.job_filter.rejection(job, now)
            signature = duplicate_signature(job)
            if reason is None and job_key(job) in keys:
                reason = "duplicada_en_corrida"
            if reason is None and signature and signature in signatures:
                reason = "duplicada_en_corrida"
            if reason is None and self.seen.is_duplicate(job):
                reason = "ya_vista_con_otro_id"
            if reason:
                report.filtered[reason] = report.filtered.get(reason, 0) + 1
                self.seen.mark(job, outcome=f"descartada:{reason}")
                continue
            keys.add(job_key(job))
            if signature:
                signatures.add(signature)
            candidates.append(job)
        self.seen.commit()
        return candidates

    @staticmethod
    def _fill_display_fields(job: JobPosting, match) -> JobPosting:
        """Use the matcher's reading of the posting only where the source text could not be parsed."""
        updates = {
            field: value
            for field, value in (
                ("title", match.posting_title),
                ("company", match.posting_company),
                ("location", match.posting_location),
            )
            if not getattr(job, field).strip() and value.strip()
        }
        return job.model_copy(update=updates) if updates else job
