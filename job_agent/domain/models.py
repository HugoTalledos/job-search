"""Domain model.

The first group of models doubles as the structured-output schema the LLM adapters ask for, which is
why the fields carry descriptions. Nothing here knows about Claude, MCP, git, Telegram or files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from job_contracts.models import (
    CollectionReport, CollectorPlan, JobLead, JobPosting, SearchPlan, SearchQuery, SourceCollection,
)


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


class JobRequirement(BaseModel):
    """An explicit posting requirement with evidence from the candidate's inputs."""

    name: str
    priority: Literal["must", "nice"]
    covered: bool
    evidence: str = Field(description="Evidence in the resume or profile, or the missing evidence for a gap")


class JobMatch(BaseModel):
    score: int = Field(description="0-100 fit between candidate and posting")
    verdict: Literal["strong", "good", "weak", "no"]
    reasons: list[str] = Field(description="Why the candidate fits")
    gaps: list[str] = Field(description="Requirements the candidate does not meet")
    requirements: list[JobRequirement] = Field(
        default_factory=list,
        description="Each explicit skill or requirement, its priority and candidate evidence or gap",
    )
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
    english_level: str = Field(
        description="English level the posting asks for, briefly (e.g. 'B2', 'Avanzado', 'Conversacional'), "
        "or empty string if it does not mention one"
    )
    salary_range: str = Field(
        description="Salary or salary range exactly as the posting states it, with currency and period, "
        "or empty string if it does not state one"
    )


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
    resume_fingerprint: str = ""  # empty in files written by older versions -> treated as changed
    repos_fingerprint: str = ""
    built_at: datetime
    repos_checked_at: datetime | None = None  # last time repository changes were looked up
    repositories: list[RepoEvidence] = Field(default_factory=list)
