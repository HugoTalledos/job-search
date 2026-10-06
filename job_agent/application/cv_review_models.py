"""Immutable review values and explicit approval outcomes."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from .cv_models import CvVersionKey
from pydantic import BaseModel, ConfigDict, Field
from ..domain.cv_corrections import FactOperation


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
    approved_key: CvVersionKey | None = None


@dataclass(frozen=True)
class ApprovalResult:
    status: Literal['approved', 'already_approved', 'stale', 'unknown']
    revision: CvRevision | None = None
    key: CvVersionKey | None = None


class TextReplacement(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    old_text: str = Field(min_length=1)
    new_text: str


class CvEditProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proposal_id: str | None = None
    replacements: list[TextReplacement]
    fact_operations: list[FactOperation]
    explanation: str


@dataclass(frozen=True)
class ProposalResolution:
    status: str
    proposal: CvEditProposal
    expected_revision_id: str
    expected_corrections_version: int
