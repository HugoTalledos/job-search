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


class ResumeChange(BaseModel):
    section: str
    change: str = Field(description="What was changed, in one sentence")
    rationale: str = Field(description="Which job requirement it addresses and what evidence backs it")


class TailoredResume(BaseModel):
    resume_markdown: str = Field(description="The full tailored resume in Markdown")
    changes: list[ResumeChange]
    summary_for_candidate: str = Field(description="2-3 sentence summary of the modifications, in Spanish")


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


class SearchCriteria(BaseModel):
    """What a job source must look for in one cycle."""

    target_roles: list[str]
    keywords: list[str]
    seniority: str
    top_skills: list[str]
    locations: list[str]
    posted_within_days: int
    max_results: int
    exclude_companies: list[str]
    already_seen_urls: list[str]


class SavedApplication(BaseModel):
    """A tailored resume persisted for one posting."""

    folder: str
    attachment: Path | None = None
    link: str | None = None


class JobAlert(BaseModel):
    """Everything the candidate is told about one posting."""

    job: JobPosting
    match: JobMatch
    tailored: TailoredResume | None = None
    application: SavedApplication | None = None


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
    resume_dir: str | None = None


class CycleReport(BaseModel):
    found: int = 0
    new: int = 0
    scored: int = 0
    notified: int = 0
    tailored: int = 0
    errors: list[str] = Field(default_factory=list)
