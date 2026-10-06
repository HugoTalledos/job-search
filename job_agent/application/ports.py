"""Ports: the interfaces the application core needs from the outside world.

Every tool the agent uses (LLM, job boards via MCP, git hosting, notifications, storage) is reached
only through one of these protocols. Adapters in ``job_agent.adapters`` implement them and
``job_agent.bootstrap`` wires them together.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

from job_contracts import CollectorPlan, SearchPreferences

from .cv_review_models import CvReview, CvRevision, ApprovalResult
from .cv_models import ClaimResult, CvArtifacts, CvVersionKey, ReadyCvVersion
from .preference_models import DraftResolution, PreferenceDraft

from ..domain.cv_corrections import CorrectionSet, FactOperation
from ..domain.preference_edits import PreferenceEdit
from ..domain.models import JobMatch, JobPosting, Profile, RepoEvidence, RepoRef, StoredProfile, TailoredResume

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


class ProfileCorrections(Protocol):
    def load(self) -> CorrectionSet: ...
    def confirm(self, operations: list[FactOperation], expected_version: int) -> CorrectionSet: ...
    def preview(self, operations: list[FactOperation], expected_version: int) -> CorrectionSet: ...
    def prepare_in_transaction(self, transaction): ...
    def apply_in_transaction(self, transaction, operations: list[FactOperation], expected_version: int, *, prepared=None) -> CorrectionSet: ...


class ProfileStore(Protocol):
    def load(self) -> StoredProfile | None: ...

    def save(self, stored: StoredProfile) -> None: ...

    def save_inferred(self, stored: StoredProfile) -> StoredProfile: ...


# --- Reasoning (LLM-backed in production) -----------------------------------------------------


class ProfileInferer(Protocol):
    def infer(self, resume_text: str, evidence: list[RepoEvidence], preferred_locations: list[str], *, corrections: CorrectionSet | None = None) -> Profile: ...


class JobMatcher(Protocol):
    def score(self, job: JobPosting, profile: Profile, resume_text: str, *, corrections: CorrectionSet | None = None) -> JobMatch: ...


class ResumeTailor(Protocol):
    def tailor(
        self,
        job: JobPosting,
        match: JobMatch,
        profile: Profile,
        resume_text: str,
        starting_from: str | None = None,
        *, corrections: CorrectionSet | None = None,
    ) -> TailoredResume:
        """Tailor the base resume to ``job``. With ``starting_from`` (an existing tailored version derived
        from the same base), make the smallest changes that make it fit the new posting."""
        ...


class PreferenceInterpreter(Protocol):
    def interpret(self, current: SearchPreferences, request: str) -> PreferenceEdit: ...


# --- Outputs -----------------------------------------------------------------------------------


class ProfileReporter(Protocol):
    """Tells the candidate how a profile build requested from chat ended."""

    def built(self, profile: Profile, changes: list[str], first_build: bool) -> None: ...

    def failed(self) -> None: ...


# --- On-demand CV generation ------------------------------------------------------------------


class CandidateProfileReader(Protocol):
    def load(self) -> Profile: ...


class JobPostingReader(Protocol):
    def load(self, posting_id: str) -> JobPosting: ...


class CvTrackingStore(Protocol):
    def claim_delivery(self, key: CvVersionKey, now: datetime, *, resend: bool = False): ...
    def renew_delivery(self, key: CvVersionKey, attempt_id: str, now: datetime) -> None: ...
    def find_ready_by_pdf_message(self, message_id: int) -> ReadyCvVersion | None: ...
    def claim(self, key: CvVersionKey, now: datetime) -> ClaimResult: ...

    def begin_delivery(self, key: CvVersionKey) -> None:
        """Reset receipts for a fully delivered version requested again; preserve partial delivery."""
        ...

    def mark_ready(
        self, key: CvVersionKey, artifacts: CvArtifacts, match: JobMatch, tailored: TailoredResume,
        *, attempt_id: str,
    ) -> None: ...

    def mark_failed(self, key: CvVersionKey, *, attempt_id: str) -> None: ...

    def mark_summary_sent(self, key: CvVersionKey, message_id: int, *, delivery_attempt_id: str | None = None) -> None: ...

    def mark_pdf_sent(self, key: CvVersionKey, message_id: int, *, delivery_attempt_id: str | None = None) -> None: ...

    def mark_delivery_failed(self, key: CvVersionKey, *, delivery_attempt_id: str | None = None) -> None: ...

    def load_ready(self, key: CvVersionKey) -> ReadyCvVersion: ...


class CvReviewStore(Protocol):
    def record_preview(self, review_id, revision_id, chat_id, preview_message_id, markdown_message_id) -> None: ...
    def resolve_preview(self, chat_id, message_id) -> tuple[str, str] | None: ...
    def save_proposal(self, review_id, expected_revision_id, expected_corrections_version, proposal) -> str: ...
    def load_proposal(self, review_id, proposal_id): ...
    def prepare_proposal_in_transaction(self, transaction, review_id, proposal_id): ...
    def prepare_revision_in_transaction(self, transaction, review_id, expected_revision_id, markdown_uri, proposal_id): ...
    def resolve_proposal_in_transaction(self, transaction, review_id, proposal_id, action, *, prepared=None): ...
    def cancel(self, review_id, chat_id) -> None: ...
    def save_context(self, review_id: str, context: dict) -> None: ...
    def load_context(self, review_id: str) -> dict: ...
    def load_revision(self, review_id: str, revision_id: str, chat_id: str) -> CvRevision: ...
    def create_or_resume(self, posting_id: str, chat_id: str, key: CvVersionKey) -> CvReview: ...
    def publish_revision(self, review_id: str, expected_revision_id: str | None, markdown_uri: str, proposal_id: str | None) -> CvRevision: ...
    def publish_revision_in_transaction(self, transaction, review_id: str, expected_revision_id: str | None, markdown_uri: str, proposal_id: str | None, *, prepared=None) -> CvRevision: ...
    def load(self, review_id: str, chat_id: str) -> CvReview: ...
    def approve(self, review_id: str, revision_id: str, chat_id: str) -> ApprovalResult: ...


class CvArtifactStore(Protocol):
    def save_markdown(self, review_id: str, revision_id: str, markdown: str) -> str: ...
    def read_markdown(self, uri: str) -> str: ...

    def save(
        self, key: CvVersionKey, pdf: bytes, markdown: str, readme: str, *, attempt_id: str,
    ) -> CvArtifacts: ...

    def read_pdf(self, artifacts: CvArtifacts) -> bytes: ...


class PdfRenderer(Protocol):
    def render(self, markdown_text: str) -> bytes: ...


class CvDelivery(Protocol):
    def send_summary(self, chat_id: str, reply_to_message_id: int, summary: str) -> int: ...

    def send_pdf(self, chat_id: str, reply_to_message_id: int, pdf: bytes, posting_id: str, *, review_id: str | None = None) -> int: ...

    def send_preview(self, chat_id: str, reply_to_message_id: int, review_id: str,
                     revision_id: str, summary: str, markdown: str): ...

    def send_edit_proposal(self, chat_id: str, reply_to_message_id: int, review_id: str,
                           proposal_id: str, before: str, after: str, scope: str) -> int: ...


# --- Search preferences -----------------------------------------------------------------------


class SearchSettingsStore(Protocol):
    """Search preferences (source of truth), the derived collector plan and pending change drafts."""

    def load_preferences(self) -> SearchPreferences | None: ...

    def load_plan(self) -> CollectorPlan | None: ...

    def seed(
        self, preferences: SearchPreferences, compile: Callable[[SearchPreferences], CollectorPlan | None],
        *, force: bool, now: datetime,
    ) -> bool:
        """Store initial preferences (and their plan); False, writing nothing, if some exist and not ``force``."""
        ...

    def save_plan_if_version(self, plan: CollectorPlan, expected_version: int) -> bool:
        """Atomically write ``plan`` only if the stored preferences are still at ``expected_version``."""
        ...

    def create_draft(self, draft: PreferenceDraft) -> None: ...

    def resolve_draft(
        self, draft_id: str, chat_id: str, action: Literal["apply", "cancel"], now: datetime,
        compile: Callable[[SearchPreferences], CollectorPlan | None],
    ) -> DraftResolution: ...
