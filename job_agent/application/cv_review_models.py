"""Immutable review values and explicit approval outcomes."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from .cv_models import CvVersionKey


@dataclass(frozen=True)
class CvRevision:
    revision_id: str
    review_id: str
    markdown_uri: str
    proposal_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class CvReview:
    review_id: str
    posting_id: str
    chat_id: str
    key: CvVersionKey
    active_revision_id: str | None = None
    approved_revision_id: str | None = None
    status: str = 'DRAFT'


@dataclass(frozen=True)
class ApprovalResult:
    status: Literal['approved', 'already_approved', 'stale', 'unknown']
    revision: CvRevision | None = None
