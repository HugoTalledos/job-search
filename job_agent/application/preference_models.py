"""Value objects for search preference changes awaiting the candidate's confirmation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

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
    created_at: datetime
    expires_at: datetime


class DraftResolution(BaseModel):
    status: Literal["applied", "cancelled", "stale", "expired", "already_resolved", "not_found"]
    previous_status: DraftStatus | None = None
    preferences_version: int | None = None
    plan: CollectorPlan | None = None  # the plan written, when one was
    plan_kept: bool = False  # applied but no keywords: previous plan kept
