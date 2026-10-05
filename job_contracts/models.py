"""Firestore document payloads shared by independently deployed components."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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


class SearchQuery(BaseModel):
    keywords: str
    location: str | None = None


class SearchPlan(BaseModel):
    queries: list[SearchQuery]
    posted_within_days: int
    work_types: list[str] = Field(default_factory=list)
    experience_levels: list[str] = Field(default_factory=list)


class JobLead(BaseModel):
    source: str
    external_id: str
    url: str = ""


class SourceCollection(BaseModel):
    jobs: list[JobPosting]
    leads: int
    known: int
    known_notified: int = 0
    known_pending: int = 0
    search_errors: list[str] = Field(default_factory=list)
    detail_errors: list[str] = Field(default_factory=list)


class CollectorPlan(BaseModel):
    search: SearchPlan
    max_details_per_run: int = Field(gt=0)
    exclude_companies: list[str] = []
    exclude_title_keywords: list[str] = []
    preferences_version: int = 0
    profile_fingerprint: str = ""
    built_at: datetime | None = None


class CollectionReport(BaseModel):
    leads: int = 0
    known: int = 0
    known_notified: int = 0
    known_pending: int = 0
    fetched: int = 0
    inserted: int = 0
    excluded: int = 0
    inference_triggered: bool = False
    errors: list[str] = Field(default_factory=list)
