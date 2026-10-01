"""Pydantic models shared across the pipeline (also used as structured-output schemas)."""

from __future__ import annotations

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
