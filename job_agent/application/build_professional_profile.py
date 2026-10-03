"""Use case: rebuild the professional profile on demand and report what changed."""

from __future__ import annotations

import logging

from ..domain.models import Profile
from ..domain.policies import profile_changes
from .build_profile import EnsureProfile
from .ports import ProfileReporter, ProfileStore

log = logging.getLogger(__name__)


class BuildProfessionalProfile:
    def __init__(self, ensure_profile: EnsureProfile, store: ProfileStore, reporter: ProfileReporter) -> None:
        self.ensure_profile = ensure_profile
        self.store = store
        self.reporter = reporter

    def execute(self) -> Profile:
        try:
            stored = self.store.load()
            previous = stored.profile if stored else None
            profile = self.ensure_profile.execute(force=True)
        except Exception:
            self.reporter.failed()
            raise
        self.reporter.built(profile, profile_changes(previous, profile), first_build=previous is None)
        return profile
