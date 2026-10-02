"""Use case: obtain an up-to-date candidate profile, re-inferring it only when it is worth the tokens."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from ..domain.models import Profile, StoredProfile
from ..domain.policies import ProfileRefreshPolicy, repos_fingerprint, resume_fingerprint
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
        refresh_days: int = 30,
        preferred_locations: list[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.resume = resume
        self.repositories = repositories
        self.inferer = inferer
        self.store = store
        self.policy = ProfileRefreshPolicy(refresh_days)
        self.preferred_locations = preferred_locations or []
        self.clock = clock

    def execute(self, force: bool = False) -> Profile:
        now = self.clock()
        resume_text = self.resume.read()
        resume_fp = resume_fingerprint(resume_text)
        stored = self.store.load()

        if not force and stored is not None:
            # Cheapest path: same resume and repositories not due for a check -> no network, no LLM.
            if stored.resume_fingerprint == resume_fp and not self.policy.repos_check_due(stored, now):
                return stored.profile

        repos = self.repositories.list_repositories()
        repos_fp = repos_fingerprint([r.url + self.repositories.head(r) for r in repos])

        reason = "reconstrucción forzada" if force else self.policy.rebuild_reason(stored, resume_fp, repos_fp, now)
        if reason is None:
            # Repositories checked and unchanged: remember the check so it is not repeated every run.
            self.store.save(stored.model_copy(update={"repos_checked_at": now}))
            return stored.profile

        log.info("Inferring profile (%s) from resume + %d repositories", reason, len(repos))
        evidence = [e for r in repos if (e := self.repositories.collect_evidence(r))]
        profile = self.inferer.infer(resume_text, evidence, self.preferred_locations)
        self.store.save(
            StoredProfile(
                profile=profile,
                resume_fingerprint=resume_fp,
                repos_fingerprint=repos_fp,
                built_at=now,
                repos_checked_at=now,
                repositories=evidence,
            )
        )
        return profile
