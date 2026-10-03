"""Use case: manage the candidate's search preferences and the collector plan compiled from them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from job_contracts import CollectorPlan, SearchPreferences

from ..domain.policies import SearchBudgets, build_search_plan
from ..domain.preference_edits import apply_operations, preference_diff
from .ports import PreferenceInterpreter, ProfileStore, SearchSettingsStore
from .preference_models import DraftResolution, PreferenceDraft, PreferencesView, Proposal, RebuildResult, SeedResult

DRAFT_TTL = timedelta(hours=24)


class ManageSearchPreferences:
    def __init__(
        self,
        *,
        store: SearchSettingsStore,
        profiles: ProfileStore,
        budgets: SearchBudgets,
        interpreter: PreferenceInterpreter | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        new_id: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.store = store
        self.profiles = profiles
        self.budgets = budgets
        self.interpreter = interpreter
        self.clock = clock
        self.new_id = new_id

    def _profile(self):
        stored = self.profiles.load()
        return stored.profile if stored is not None else None

    def _compile(self, preferences: SearchPreferences) -> CollectorPlan | None:
        return build_search_plan(preferences, self._profile(), self.budgets, self.clock()).plan

    def seed(self, preferences: SearchPreferences, *, force: bool) -> SeedResult:
        """Publish the initial preferences and their plan; not created if some exist and ``force`` is not set."""
        compiled: list[CollectorPlan | None] = []

        def compile_and_record(p: SearchPreferences) -> CollectorPlan | None:
            plan = self._compile(p)
            compiled.append(plan)  # a transaction may retry: the last attempt is the committed one
            return plan

        created = self.store.seed(preferences, compile_and_record, force=force, now=self.clock())
        return SeedResult(created=created, plan_written=created and bool(compiled) and compiled[-1] is not None)

    def show(self) -> PreferencesView:
        return PreferencesView(preferences=self.store.load_preferences(), plan=self.store.load_plan())

    def propose(self, request: str, chat_id: str) -> Proposal:
        """Interpret a free-text request into a pending draft; the text itself is never stored."""
        if self.interpreter is None:
            raise RuntimeError("ManageSearchPreferences.propose requires a preference interpreter")
        current = self.store.load_preferences()
        if current is None:
            return Proposal(kind="missing_preferences")
        outcome = apply_operations(current, self.interpreter.interpret(current, request))
        if outcome.preferences is None:
            return Proposal(kind="rejected", problems=outcome.problems)
        now = self.clock()
        draft = PreferenceDraft(
            draft_id=self.new_id(), chat_id=chat_id, base_version=current.version,
            preferences=outcome.preferences, diff=preference_diff(current, outcome.preferences),
            created_at=now, expires_at=now + DRAFT_TTL,
        )
        self.store.create_draft(draft)
        build = build_search_plan(outcome.preferences, self._profile(), self.budgets, now)
        return Proposal(
            kind="draft", draft=draft, plan_preview=build.plan, total_queries=build.total_queries,
            problems=outcome.problems,
        )

    def resolve(self, draft_id: str, chat_id: str, action: Literal["apply", "cancel"]) -> DraftResolution:
        profile = self._profile()
        now = self.clock()
        return self.store.resolve_draft(
            draft_id, chat_id, action, now,
            compile=lambda p: build_search_plan(p, profile, self.budgets, now).plan,
        )

    def rebuild_plan(self) -> RebuildResult:
        preferences = self.store.load_preferences()
        if preferences is None:
            return RebuildResult(status="no_preferences")
        plan = self._compile(preferences)
        if plan is None:
            return RebuildResult(status="no_keywords")
        if not self.store.save_plan_if_version(plan, preferences.version):
            return RebuildResult(status="superseded")  # an Apply committed meanwhile and wrote its own plan
        return RebuildResult(status="rebuilt", plan=plan)
