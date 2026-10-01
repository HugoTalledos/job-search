"""Pure business rules: posting identity, de-duplication, notify/tailor thresholds, profile freshness."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import JobMatch, JobPosting, Profile, SearchCriteria, StoredProfile


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def job_key(job: JobPosting) -> str:
    """Stable id: source id when present, else canonical url, else company+title."""
    if job.external_id:
        basis = f"{_norm(job.source)}:{job.external_id}"
    elif job.url:
        basis = re.sub(r"[?#].*$", "", job.url.strip().lower()).rstrip("/")
    else:
        basis = duplicate_signature(job)
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


def duplicate_signature(job: JobPosting) -> str:
    """Identifies the same role reposted under a new id or on another board."""
    return f"{_norm(job.company)}|{_norm(job.title)}"


@dataclass(frozen=True)
class MatchingPolicy:
    min_score_to_notify: int = 70
    min_score_to_tailor: int = 70

    def should_notify(self, match: JobMatch) -> bool:
        return match.score >= self.min_score_to_notify

    def should_tailor(self, match: JobMatch) -> bool:
        return self.should_notify(match) and match.resume_undersells and match.score >= self.min_score_to_tailor


@dataclass(frozen=True)
class SearchPreferences:
    locations: tuple[str, ...] = ("Remote",)
    extra_keywords: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()
    posted_within_days: int = 2
    max_jobs_per_run: int = 25

    def criteria_for(self, profile: Profile, already_seen_urls: list[str]) -> SearchCriteria:
        return SearchCriteria(
            target_roles=profile.target_roles,
            keywords=[*profile.search_keywords, *self.extra_keywords],
            seniority=profile.seniority,
            top_skills=[s.name for s in profile.skills[:15]],
            locations=list(self.locations or profile.locations),
            posted_within_days=self.posted_within_days,
            max_results=self.max_jobs_per_run,
            exclude_companies=list(self.exclude_companies),
            already_seen_urls=already_seen_urls[-300:],
        )

    def is_excluded(self, job: JobPosting) -> bool:
        return job.company.strip().lower() in {c.strip().lower() for c in self.exclude_companies}


def profile_fingerprint(resume_text: str, repo_heads: list[str]) -> str:
    h = hashlib.sha256(resume_text.encode())
    for head in sorted(repo_heads):
        h.update(head.encode())
    return h.hexdigest()


def profile_is_current(stored: StoredProfile, fingerprint: str, now: datetime, refresh_days: int) -> bool:
    return stored.fingerprint == fingerprint and now - stored.built_at < timedelta(days=refresh_days)
