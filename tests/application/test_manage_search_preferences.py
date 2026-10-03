from datetime import datetime, timezone

from job_contracts import SearchPreferences

from job_agent.application.manage_search_preferences import ManageSearchPreferences
from job_agent.domain.models import StoredProfile
from job_agent.domain.policies import SearchBudgets
from tests.fakes import MemoryProfileStore

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


class SeedStore:
    def __init__(self):
        self.prefs = None
        self.plan = None

    def seed(self, preferences, compile, *, force, now):
        if self.prefs is not None and not force:
            return False
        self.prefs = preferences.model_copy(update={"version": 1, "updated_at": now})
        self.plan = compile(self.prefs)
        return True


def _use_case(store, profiles):
    return ManageSearchPreferences(store=store, profiles=profiles, budgets=SearchBudgets(max_queries=2), clock=lambda: NOW)


def test_seed_compiles_plan_and_does_not_overwrite_without_force(profile):
    profiles = MemoryProfileStore()
    profiles.stored = StoredProfile(
        profile=profile, resume_fingerprint="r", repos_fingerprint="p", built_at=NOW, repos_checked_at=NOW, repositories=[]
    )
    store = SeedStore()
    uc = _use_case(store, profiles)

    assert uc.seed(SearchPreferences(keywords_include=["Django"], locations=["Remote"]), force=False) is True
    assert [q.keywords for q in store.plan.search.queries] == ["Django", profile.target_roles[0]]
    assert len(store.plan.search.queries) == 2  # budget applied

    assert uc.seed(SearchPreferences(keywords_include=["Rust"]), force=False) is False
    assert store.prefs.keywords_include == ["Django"]
    assert uc.seed(SearchPreferences(keywords_include=["Rust"]), force=True) is True
    assert store.prefs.keywords_include == ["Rust"]


def test_seed_without_profile_or_keywords_writes_no_plan():
    store = SeedStore()
    assert _use_case(store, MemoryProfileStore()).seed(SearchPreferences(), force=False) is True
    assert store.plan is None
