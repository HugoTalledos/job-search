"""Use case: obtain an up-to-date candidate profile, re-inferring it only when inputs changed."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from ..domain.models import Profile, StoredProfile
from ..domain.policies import profile_fingerprint, profile_is_current
from .ports import CodeRepositoryReader, ProfileInferer, ProfileStore, ResumeSource

log = logging.getLogger(__name__)


class EnsureProfile:
    def __init__(
        self,
        resume: ResumeSource,
        repositories: CodeRepositoryReader,
        inferer: ProfileInferer,
        store: ProfileStore,
        *,
        refresh_days: int = 7,
        preferred_locations: list[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.resume = resume
        self.repositories = repositories
        self.inferer = inferer
        self.store = store
        self.refresh_days = refresh_days
        self.preferred_locations = preferred_locations or []
        self.clock = clock

    def execute(self, force: bool = False) -> Profile:
        resume_text = self.resume.read()
        repos = self.repositories.list_repositories()
        fingerprint = profile_fingerprint(resume_text, [r.url + self.repositories.head(r) for r in repos])
        now = self.clock()

        stored = None if force else self.store.load()
        if stored and profile_is_current(stored, fingerprint, now, self.refresh_days):
            return stored.profile

        log.info("Inferring profile from resume + %d repositories", len(repos))
        evidence = [e for r in repos if (e := self.repositories.collect_evidence(r))]
        profile = self.inferer.infer(resume_text, evidence, self.preferred_locations)
        self.store.save(StoredProfile(profile=profile, fingerprint=fingerprint, built_at=now, repositories=evidence))
        return profile
