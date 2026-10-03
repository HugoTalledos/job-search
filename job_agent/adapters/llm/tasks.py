"""Implementations of the LLM-backed application ports, on top of any StructuredModel provider."""

from __future__ import annotations

from job_contracts import SearchPreferences

from ...domain.preference_edits import PreferenceEdit
from ...domain.models import (
    JobMatch,
    JobPosting,
    Profile,
    RepoEvidence,
    TailoredResume,
)
from . import prompts
from .structured import StructuredModel


class LlmProfileInferer:
    """ProfileInferer port."""

    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    def infer(self, resume_text: str, evidence: list[RepoEvidence], preferred_locations: list[str]) -> Profile:
        return self.model.complete(
            system=prompts.PROFILE_SYSTEM,
            content=prompts.profile_content(resume_text, evidence, preferred_locations),
            schema=Profile,
            effort="high",
        )


class LlmJobMatcher:
    """JobMatcher port."""

    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    def score(self, job: JobPosting, profile: Profile, resume_text: str) -> JobMatch:
        return self.model.complete(
            system=prompts.MATCH_SYSTEM,
            content=prompts.match_content(job, profile, resume_text),
            schema=JobMatch,
            effort="low",
            max_tokens=8000,
        )


class LlmResumeTailor:
    """ResumeTailor port."""

    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    def tailor(
        self,
        job: JobPosting,
        match: JobMatch,
        profile: Profile,
        resume_text: str,
        starting_from: str | None = None,
    ) -> TailoredResume:
        return self.model.complete(
            system=prompts.TAILOR_SYSTEM,
            content=prompts.tailor_content(job, match, profile, resume_text, starting_from),
            schema=TailoredResume,
            effort="high",
            max_tokens=16000,
        )


class LlmPreferenceInterpreter:
    """PreferenceInterpreter port."""

    def __init__(self, model: StructuredModel) -> None:
        self.model = model

    def interpret(self, current: SearchPreferences, request: str) -> PreferenceEdit:
        return self.model.complete(
            system=prompts.PREFERENCES_SYSTEM,
            content=prompts.preferences_content(current, request),
            schema=PreferenceEdit,
            effort="low",
            max_tokens=4000,
        )
