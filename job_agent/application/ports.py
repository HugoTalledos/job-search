"""Ports: the interfaces the application core needs from the outside world.

Every tool the agent uses (LLM, job boards via MCP, git hosting, notifications, storage) is reached
only through one of these protocols. Adapters in ``job_agent.adapters`` implement them and
``job_agent.bootstrap`` wires them together.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ..domain.models import (
    JobAlert,
    JobLead,
    JobMatch,
    JobPosting,
    MatchRecord,
    Profile,
    RepoEvidence,
    ResumeVersion,
    ReuseDecision,
    RepoRef,
    SavedApplication,
    SearchPlan,
    StoredProfile,
    TailoredResume,
)

# --- Candidate data ----------------------------------------------------------------------------


class ResumeSource(Protocol):
    """Where the candidate's base resume lives."""

    def read(self) -> str: ...


class CodeRepositoryReader(Protocol):
    """Access to the candidate's code repositories (GitHub, GitLab, any git remote...)."""

    def list_repositories(self) -> list[RepoRef]: ...

    def head(self, repo: RepoRef) -> str:
        """Current commit id, cheap to obtain; empty string if unknown."""
        ...

    def collect_evidence(self, repo: RepoRef) -> RepoEvidence | None: ...


class ProfileStore(Protocol):
    def load(self) -> StoredProfile | None: ...

    def save(self, stored: StoredProfile) -> None: ...


# --- Reasoning (LLM-backed in production) -----------------------------------------------------


class ProfileInferer(Protocol):
    def infer(self, resume_text: str, evidence: list[RepoEvidence], preferred_locations: list[str]) -> Profile: ...


class JobMatcher(Protocol):
    def score(self, job: JobPosting, profile: Profile, resume_text: str) -> JobMatch: ...


class ResumeTailor(Protocol):
    def tailor(
        self,
        job: JobPosting,
        match: JobMatch,
        profile: Profile,
        resume_text: str,
        starting_from: str | None = None,
    ) -> TailoredResume:
        """Tailor the base resume to ``job``. With ``starting_from`` (an existing tailored version derived
        from the same base), make the smallest changes that make it fit the new posting."""
        ...


class ResumeSelector(Protocol):
    """Decides whether a stored tailored resume can serve a new posting."""

    def choose(self, job: JobPosting, match: JobMatch, candidates: list[ResumeVersion]) -> ReuseDecision: ...


# --- Job market ---------------------------------------------------------------------------------


class CollectedPostingsRepository(Protocol):
    """Offers stored by the local collector for later evaluation."""

    def list_postings(self) -> list[JobPosting]: ...


@runtime_checkable
class JobSource(Protocol):
    """A job board queried deterministically (no LLM): LinkedIn via its MCP server, a job-board API...

    ``collect`` runs every query of the plan, asks ``admit`` about each id it finds (the application
    rejects ids already processed or repeated, before any details are fetched) and fetches the details of
    at most ``max_details`` admitted ids.
    """

    @property
    def name(self) -> str: ...

    def collect(self, plan: SearchPlan, admit: Callable[[JobLead], bool], max_details: int) -> list[JobPosting]: ...


# --- Outputs -----------------------------------------------------------------------------------


class ApplicationStore(Protocol):
    """Catalogue of tailored resumes: persist (rendered), list, load and track reuse."""

    def save(
        self,
        job: JobPosting,
        match: JobMatch,
        tailored: TailoredResume,
        base_fingerprint: str,
        adapted_from: str | None = None,
    ) -> SavedApplication: ...

    def list_versions(self) -> list[ResumeVersion]: ...

    def load_markdown(self, version_id: str) -> str: ...

    def locate(self, version_id: str) -> SavedApplication: ...

    def record_use(self, version_id: str, job: JobPosting) -> None: ...


class Notifier(Protocol):
    def notify(self, alert: JobAlert) -> None: ...


class SeenJobsRepository(Protocol):
    """Postings already processed (scored or discarded by a filter)."""

    def is_seen_key(self, key: str) -> bool:
        """Known id; also refreshes its last-seen date so postings still online are not forgotten."""
        ...

    def is_duplicate(self, job: JobPosting) -> bool:
        """Same company + role (normalised) as a processed posting, under another id or source."""
        ...

    def mark(self, job: JobPosting, outcome: str, score: int | None = None) -> None: ...

    def commit(self) -> None:
        """Flush pending changes to durable storage."""
        ...


class MatchHistory(Protocol):
    def append(self, record: MatchRecord) -> None: ...
