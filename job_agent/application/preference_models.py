"""Value objects for search preference changes awaiting the candidate's confirmation."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel

from job_contracts import CollectorPlan, SearchPreferences

DraftStatus = Literal["PENDING", "APPLIED", "CANCELLED", "EXPIRED"]


class PreferenceDraft(BaseModel):
    """A proposed change stored at ``search_preference_drafts/{draft_id}``; free text is never kept."""

    draft_id: str
    chat_id: str
    base_version: int
    preferences: SearchPreferences
    diff: list[str]
    status: DraftStatus = "PENDING"
    created_at: AwareDatetime
    expires_at: AwareDatetime


class DraftResolution(BaseModel):
    status: Literal["applied", "cancelled", "stale", "expired", "already_resolved", "not_found"]
    previous_status: DraftStatus | None = None
    preferences_version: int | None = None
    plan: CollectorPlan | None = None  # the plan written, when one was
    plan_kept: bool = False  # applied but no keywords: previous plan kept


class PreferencesView(BaseModel):
    preferences: SearchPreferences | None
    plan: CollectorPlan | None


class Proposal(BaseModel):
    kind: Literal["draft", "rejected", "missing_preferences"]
    draft: PreferenceDraft | None = None
    plan_preview: CollectorPlan | None = None
    total_queries: int = 0
    problems: list[str] = []


class SeedResult(BaseModel):
    created: bool  # False: preferences already existed and nothing was written
    plan_written: bool = False  # False with created: no keywords nor profile, the previous plan is kept


class RebuildResult(BaseModel):
    # superseded: the preferences changed while rebuilding; that change already wrote its own plan.
    status: Literal["rebuilt", "no_keywords", "no_preferences", "superseded"]
    plan: CollectorPlan | None = None
