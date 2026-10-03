"""Values shared by the on-demand CV workflow and its persistence ports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from ..domain.models import JobMatch, JobPosting, Profile, TailoredResume

ClaimAction = Literal['generate', 'reuse', 'in_progress']
GenerationStatus = Literal['PROCESSING', 'READY', 'FAILED']
DeliveryStatus = Literal['PENDING', 'SENT', 'FAILED']


@dataclass(frozen=True)
class CvVersionKey:
    posting_id: str
    resume_fingerprint: str
    profile_fingerprint: str
    job_fingerprint: str

    @property
    def version_id(self) -> str:
        # A structured encoding keeps adjacent input boundaries unambiguous.
        inputs = [self.posting_id, self.resume_fingerprint, self.profile_fingerprint, self.job_fingerprint]
        return hashlib.sha256(json.dumps(inputs, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class CvArtifacts:
    pdf_uri: str
    markdown_uri: str
    readme_uri: str


@dataclass(frozen=True)
class ClaimResult:
    action: ClaimAction
    attempt_id: str | None = None


@dataclass(frozen=True)
class PreparedCvRequest:
    key: CvVersionKey
    action: ClaimAction
    resume_text: str
    profile: Profile
    posting: JobPosting
    attempt_id: str | None = None


@dataclass(frozen=True)
class CvGenerationResult:
    key: CvVersionKey
    generation_status: GenerationStatus
    delivery_status: DeliveryStatus


@dataclass(frozen=True)
class ReadyCvVersion:
    key: CvVersionKey
    artifacts: CvArtifacts
    match: JobMatch
    tailored: TailoredResume
    summary_message_id: int | None = None
    pdf_message_id: int | None = None
    delivery_status: DeliveryStatus = 'PENDING'
