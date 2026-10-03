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


# --- show / propose / resolve / rebuild -----------------------------------------------------------

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402

from job_agent.application.preference_models import PreferenceDraft  # noqa: E402
from job_agent.domain.preference_edits import PreferenceEdit, PreferenceOperation  # noqa: E402
from tests.fakes import MemorySearchSettings  # noqa: E402


class RecordingInterpreter:
    def __init__(self, operations=None):
        self.calls = []
        self.operations = operations or [PreferenceOperation(action="add", field="keywords_include", values=["Go"], explanation="")]

    def interpret(self, current, request):
        self.calls.append(request)
        return PreferenceEdit(operations=self.operations)


@pytest.fixture
def store():
    s = MemorySearchSettings()
    s.seed(SearchPreferences(keywords_include=["Django"]), lambda p: None, force=False, now=NOW)
    return s


@pytest.fixture
def profiles(profile):
    p = MemoryProfileStore()
    p.stored = StoredProfile(
        profile=profile, resume_fingerprint="r", repos_fingerprint="p", built_at=NOW, repos_checked_at=NOW, repositories=[]
    )
    return p


@pytest.fixture
def interpreter():
    return RecordingInterpreter()


@pytest.fixture
def service(store, profiles, interpreter):
    ids = iter(f"d{i}" for i in range(100))
    return ManageSearchPreferences(
        store=store, profiles=profiles, budgets=SearchBudgets(max_queries=5), interpreter=interpreter,
        clock=lambda: NOW, new_id=lambda: next(ids),
    )


def test_show_never_calls_interpreter(service, interpreter):
    view = service.show()
    assert interpreter.calls == [] and view.preferences.version == 1 and view.plan is None


def test_propose_stores_draft_without_free_text(service, store):
    proposal = service.propose("quiero Go remoto", "42")
    assert proposal.kind == "draft" and store.drafts[proposal.draft.draft_id].base_version == 1
    assert "quiero Go remoto" not in repr(store.drafts)
    assert proposal.draft.expires_at - proposal.draft.created_at == timedelta(hours=24)
    assert proposal.draft.diff and proposal.total_queries >= 1
    assert "Go" in [q.keywords for q in proposal.plan_preview.search.queries]


def test_propose_partial_apply_reports_unclear_problems(store, profiles):
    ops = [
        PreferenceOperation(action="add", field="keywords_include", values=["Go"], explanation=""),
        PreferenceOperation(action="unclear", field="none", values=[], explanation="No sé qué es «lo bueno»."),
    ]
    uc = ManageSearchPreferences(
        store=store, profiles=profiles, budgets=SearchBudgets(max_queries=5),
        interpreter=RecordingInterpreter(ops), clock=lambda: NOW,
    )
    proposal = uc.propose("Go y lo bueno", "42")
    assert proposal.kind == "draft" and proposal.problems == ["No sé qué es «lo bueno»."]


def test_propose_rejected_when_nothing_changes(store, profiles):
    ops = [PreferenceOperation(action="add", field="keywords_include", values=["django"], explanation="")]
    uc = ManageSearchPreferences(
        store=store, profiles=profiles, budgets=SearchBudgets(), interpreter=RecordingInterpreter(ops), clock=lambda: NOW
    )
    proposal = uc.propose("django", "42")
    assert proposal.kind == "rejected" and proposal.problems and store.drafts == {}


def test_propose_without_preferences_asks_for_migration(profiles, interpreter):
    uc = ManageSearchPreferences(
        store=MemorySearchSettings(), profiles=profiles, budgets=SearchBudgets(), interpreter=interpreter, clock=lambda: NOW
    )
    assert uc.propose("x", "42").kind == "missing_preferences"
    assert interpreter.calls == []


def test_propose_without_interpreter_raises(store, profiles):
    uc = ManageSearchPreferences(store=store, profiles=profiles, budgets=SearchBudgets(), clock=lambda: NOW)
    with pytest.raises(RuntimeError):
        uc.propose("x", "42")


def test_draft_requires_timezone_aware_datetimes(service):
    draft = service.propose("Go", "42").draft
    with pytest.raises(ValueError):
        PreferenceDraft(**{**draft.model_dump(), "created_at": datetime(2026, 10, 3)})


def test_apply_twice_applies_once(service, store):
    draft_id = service.propose("Go", "42").draft.draft_id
    first = service.resolve(draft_id, "42", "apply")
    second = service.resolve(draft_id, "42", "apply")
    assert first.status == "applied" and first.preferences_version == 2
    assert [q.keywords for q in store.plan.search.queries][0] == "Django"
    assert second.status == "already_resolved" and store.prefs.version == 2


def test_cancel_and_wrong_chat(service, store):
    draft_id = service.propose("Go", "42").draft.draft_id
    assert service.resolve(draft_id, "99", "apply").status == "not_found"
    assert service.resolve(draft_id, "42", "cancel").status == "cancelled"
    assert store.prefs.version == 1


def test_apply_without_keywords_keeps_previous_plan(store, profiles):
    store.plan = "OLD"
    ops = [
        PreferenceOperation(action="set", field="keywords_include", values=[], explanation=""),
        PreferenceOperation(action="set", field="use_profile_keywords", values=["false"], explanation=""),
    ]
    uc = ManageSearchPreferences(
        store=store, profiles=ManageProfiles(), budgets=SearchBudgets(), interpreter=RecordingInterpreter(ops),
        clock=lambda: NOW,
    )
    proposal = uc.propose("nada", "42")
    assert proposal.kind == "draft" and proposal.plan_preview is None
    result = uc.resolve(proposal.draft.draft_id, "42", "apply")
    assert result.status == "applied" and result.plan_kept is True and store.plan == "OLD"


def ManageProfiles():
    return MemoryProfileStore()


def test_rebuild_after_profile_change_uses_new_profile(service, store, profiles, profile):
    assert service.rebuild_plan().status == "rebuilt"
    before = [q.keywords for q in store.plan.search.queries]
    profiles.stored = profiles.stored.model_copy(
        update={"profile": profile.model_copy(update={"target_roles": ["Platform Engineer"]})}
    )
    service.rebuild_plan()
    after = [q.keywords for q in store.plan.search.queries]
    assert "Platform Engineer" in after and "Platform Engineer" not in before


def test_rebuild_statuses(profiles):
    empty = ManageSearchPreferences(
        store=MemorySearchSettings(), profiles=profiles, budgets=SearchBudgets(), clock=lambda: NOW
    )
    assert empty.rebuild_plan().status == "no_preferences"
    store = MemorySearchSettings()
    store.seed(SearchPreferences(use_profile_keywords=False), lambda p: None, force=False, now=NOW)
    store.plan = "OLD"
    uc = ManageSearchPreferences(store=store, profiles=MemoryProfileStore(), budgets=SearchBudgets(), clock=lambda: NOW)
    assert uc.rebuild_plan().status == "no_keywords" and store.plan == "OLD"
