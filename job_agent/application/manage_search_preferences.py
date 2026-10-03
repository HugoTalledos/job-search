"""Use case: manage the candidate's search preferences and the collector plan compiled from them."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from job_contracts import CollectorPlan, SearchPreferences

from ..domain.policies import SearchBudgets, build_search_plan
from .ports import ProfileStore, SearchSettingsStore


class ManageSearchPreferences:
    def __init__(
        self,
        *,
        store: SearchSettingsStore,
        profiles: ProfileStore,
        budgets: SearchBudgets,
        interpreter: object | None = None,  # PreferenceInterpreter port, introduced with the interpreter
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.store = store
        self.profiles = profiles
        self.budgets = budgets
        self.interpreter = interpreter
        self.clock = clock
        self.new_id = new_id

    def _compile(self, preferences: SearchPreferences) -> CollectorPlan | None:
        stored = self.profiles.load()
        profile = stored.profile if stored is not None else None
        return build_search_plan(preferences, profile, self.budgets, self.clock()).plan

    def seed(self, preferences: SearchPreferences, *, force: bool) -> bool:
        """Publish the initial preferences and their plan; False if some exist and ``force`` is not set."""
        return self.store.seed(preferences, self._compile, force=force, now=self.clock())
