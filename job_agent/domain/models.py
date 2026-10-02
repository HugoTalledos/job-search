"""Domain model.

The first group of models doubles as the structured-output schema the LLM adapters ask for, which is
why the fields carry descriptions. Nothing here knows about Claude, MCP, git, Telegram or files.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Skill(BaseModel):
    name: str
    level: Literal["basic", "intermediate", "advanced", "expert"]
    evidence: str = Field(description="Where this skill is demonstrated: resume section, repo name, etc.")


class Profile(BaseModel):
    """Candidate profile inferred from the resume and repositories."""

    full_name: str
    headline: str = Field(description="One-line professional headline")
    seniority: Literal["intern", "junior", "mid", "senior", "staff", "principal", "manager", "director"]
    years_of_experience: float
    summary: str
    target_roles: list[str] = Field(description="Job titles that fit this candidate, most relevant first")
    search_keywords: list[str] = Field(description="Short keyword queries to use on job boards")
    skills: list[Skill]
    domains: list[str] = Field(description="Industries / problem domains the candidate has worked in")
    languages: list[str] = Field(description="Spoken languages with level, e.g. 'English (C1)'")
    locations: list[str] = Field(description="Preferred locations, or 'Remote'")
    notable_projects: list[str] = Field(description="One line per notable project, mentioning repo when applicable")
    strengths_missing_from_resume: list[str] = Field(
        description="Things the repositories prove that the resume does not mention or undersells"
    )


class JobPosting(BaseModel):
    source: str = Field(description="Where the posting was found, e.g. 'linkedin' or the site domain")
    external_id: str = Field(description="ID in the source system, or empty string if unknown")
    title: str
    company: str
    location: str
    url: str
    remote: Literal["remote", "hybrid", "onsite", "unknown"]
    posted_at: str = Field(description="Posting date as reported by the source, or empty string")
    description: str = Field(description="Full job description text as returned by the source (do not summarise)")


class JobSearchResult(BaseModel):
    jobs: list[JobPosting]


class JobMatch(BaseModel):
    score: int = Field(description="0-100 fit between candidate and posting")
    verdict: Literal["strong", "good", "weak", "no"]
    reasons: list[str] = Field(description="Why the candidate fits")
    gaps: list[str] = Field(description="Requirements the candidate does not meet")
    resume_undersells: bool = Field(
        description="True when the candidate really fits but the current resume does not show it well enough"
    )
    tailoring_focus: list[str] = Field(
        description="Concrete, truthful things to emphasise in a tailored resume (empty if none)"
    )
    # Display-only: copied from the posting text so notifications read well when the source's text could
    # not be parsed. Filtering never relies on these.
    posting_title: str = Field(description="Job title exactly as written in the posting")
    posting_company: str = Field(description="Hiring company exactly as written in the posting")
    posting_location: str = Field(description="Location as written in the posting, or empty string")


class ResumeChange(BaseModel):
    section: str
    change: str = Field(description="What was changed, in one sentence")
    rationale: str = Field(description="Which job requirement it addresses and what evidence backs it")


class TailoredResume(BaseModel):
    resume_markdown: str = Field(description="The full tailored resume in Markdown")
    language: str = Field(description="ISO 639-1 code of the language the resume is written in, e.g. 'es' or 'en'")
    highlights: list[str] = Field(
        description="Skills, technologies and requirements this version puts forward (short phrases)"
    )
    changes: list[ResumeChange]
    summary_for_candidate: str = Field(description="2-3 sentence summary of the modifications, in Spanish")


class ReuseDecision(BaseModel):
    """Whether an already tailored resume can serve a new posting."""

    action: Literal["reuse", "adapt", "create"] = Field(
        description="reuse: an existing version already covers the key requirements and is in the posting's "
        "language; adapt: one is close and needs small changes; create: none is close enough"
    )
    version_id: str = Field(description="id of the chosen version for reuse/adapt; empty string for create")
    covered_requirements: list[str] = Field(description="Key requirements of the posting the version already shows")
    missing_requirements: list[str] = Field(description="Key requirements the version does not show")
    rationale: str = Field(description="One or two sentences explaining the decision, in Spanish")


# --- Entities and value objects that never go through the LLM -------------------------------------


class RepoRef(BaseModel):
    """A code repository to analyse (GitHub or any git remote)."""

    url: str
    branch: str | None = None


class RepoEvidence(BaseModel):
    """What a repository proves about the candidate (languages, structure, README, manifests)."""

    url: str
    head: str
    summary: str


class StoredProfile(BaseModel):
    profile: Profile
    fingerprint: str
    built_at: datetime
    repositories: list[RepoEvidence] = Field(default_factory=list)


class SearchQuery(BaseModel):
    """One deterministic query sent to a job source."""

    keywords: str
    location: str | None = None


class SearchPlan(BaseModel):
    """Queries plus the filters every source applies natively (when it supports them)."""

    queries: list[SearchQuery]
    posted_within_days: int
    work_types: list[str] = Field(default_factory=list)  # remote, hybrid, on_site
    experience_levels: list[str] = Field(default_factory=list)  # internship, entry, associate, mid_senior...


class JobLead(BaseModel):
    """A posting identifier returned by a search, before its details are fetched."""

    source: str
    external_id: str
    url: str = ""


class ResumeUse(BaseModel):
    job_key: str
    title: str
    company: str
    at: datetime


class ResumeVersion(BaseModel):
    """Catalogue entry of a tailored resume, used to decide whether it can be reused."""

    id: str
    job_key: str
    job_title: str
    company: str
    language: str
    highlights: list[str]
    base_fingerprint: str = Field(description="Fingerprint of the base resume this version was derived from")
    created_at: datetime
    adapted_from: str | None = None
    used_for: list[ResumeUse] = Field(default_factory=list)


class SavedApplication(BaseModel):
    """Where a tailored resume lives: folder, file to attach, web link."""

    version_id: str
    folder: str
    attachment: Path | None = None
    link: str | None = None


ResumeOrigin = Literal["created", "adapted", "reused"]


class JobAlert(BaseModel):
    """Everything the candidate is told about one posting."""

    job: JobPosting
    match: JobMatch
    tailored: TailoredResume | None = None  # the changes made in this cycle (created / adapted)
    application: SavedApplication | None = None
    resume_origin: ResumeOrigin | None = None
    source_version: ResumeVersion | None = None  # version reused or adapted
    reuse_rationale: str | None = None


class MatchRecord(BaseModel):
    at: datetime
    key: str
    title: str
    company: str
    url: str
    source: str
    score: int
    verdict: str
    tailored: bool
    resume_origin: str | None = None
    resume_dir: str | None = None


class CycleReport(BaseModel):
    leads: int = 0  # ids returned by the searches
    known_leads: int = 0  # discarded before fetching details (already seen or repeated in this run)
    fetched: int = 0  # details fetched
    filtered: dict[str, int] = Field(default_factory=dict)  # deterministic filter -> postings discarded
    candidates: int = 0  # passed every deterministic filter (sent to Claude, up to max_jobs_per_run)
    scored: int = 0
    notified: int = 0
    tailored: int = 0
    adapted: int = 0
    reused: int = 0
    errors: list[str] = Field(default_factory=list)
