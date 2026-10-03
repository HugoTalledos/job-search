from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from job_contracts import CollectorPlan, SearchPlan, SearchPreferences, SearchQuery
from job_agent.application.preference_models import PreferenceDraft
from tests.adapters.test_firestore_cv_tracking import Client

PREFS = "settings/search_preferences"
PLAN = "settings/search_plan"
NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def compile_plan(preferences: SearchPreferences) -> CollectorPlan | None:
    if not preferences.keywords_include:
        return None
    return CollectorPlan(
        search=SearchPlan(
            queries=[SearchQuery(keywords=k) for k in preferences.keywords_include],
            posted_within_days=preferences.posted_within_days,
        ),
        max_details_per_run=20,
        exclude_companies=preferences.exclude_companies,
        preferences_version=preferences.version,
        built_at=NOW,
    )


def no_plan(preferences):
    return None


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def store(client):
    from job_agent.adapters.persistence import FirestoreSearchSettingsStore
    return FirestoreSearchSettingsStore(client)


def draft(draft_id="d1", *, chat_id="42", base_version=1, keywords=("Rust",), expires_at=None):
    return PreferenceDraft(
        draft_id=draft_id, chat_id=chat_id, base_version=base_version,
        preferences=SearchPreferences(keywords_include=list(keywords), exclude_companies=["Acme"]),
        diff=["+ Rust"], created_at=NOW, expires_at=expires_at or NOW + timedelta(hours=24),
    )


def seeded(store, keywords=("Go",)):
    assert store.seed(SearchPreferences(keywords_include=list(keywords)), compile_plan, force=False, now=NOW)


def draft_doc(client, draft_id="d1"):
    return client.docs[f"search_preference_drafts/{draft_id}"]


def test_seed_creates_version_one_and_the_plan(store, client):
    seeded(store)
    prefs = store.load_preferences()
    assert prefs.version == 1 and prefs.updated_at == NOW and prefs.keywords_include == ["Go"]
    plan = store.load_plan()
    assert [q.keywords for q in plan.search.queries] == ["Go"] and plan.preferences_version == 1


def test_seed_without_force_on_existing_writes_nothing(store, client):
    seeded(store)
    before = dict(client.revisions)
    assert store.seed(SearchPreferences(keywords_include=["Rust"]), compile_plan, force=False, now=NOW) is False
    assert client.revisions == before
    assert store.load_preferences().keywords_include == ["Go"]


def test_seed_with_force_bumps_version(store):
    seeded(store)
    assert store.seed(SearchPreferences(keywords_include=["Rust"]), compile_plan, force=True, now=NOW) is True
    assert store.load_preferences().version == 2
    assert store.load_plan().preferences_version == 2


def test_seed_without_plan_keeps_no_plan_document(store, client):
    assert store.seed(SearchPreferences(), no_plan, force=False, now=NOW) is True
    assert PLAN not in client.docs and store.load_plan() is None


def test_plan_document_loads_through_the_collector_validation(store, client):
    seeded(store)
    from local_collector.adapters.firestore_store import FirestoreCollectorStore
    data = client.docs[PLAN]
    assert isinstance(data["built_at"], str)  # written with model_dump(mode="json")
    expected = compile_plan(store.load_preferences())
    assert CollectorPlan.model_validate(data) == expected
    assert FirestoreCollectorStore(client).load_plan() == expected


def test_save_plan_if_version_writes_when_version_matches(store, client):
    seeded(store)
    plan = compile_plan(SearchPreferences(keywords_include=["Rust"], version=1))
    assert store.save_plan_if_version(plan, 1) is True
    assert client.docs[PLAN] == plan.model_dump(mode="json")
    assert store.load_plan() == plan


def test_save_plan_if_version_writes_nothing_on_mismatch(store, client):
    seeded(store)
    before = dict(client.revisions)
    plan = compile_plan(SearchPreferences(keywords_include=["Rust"], version=0))
    assert store.save_plan_if_version(plan, 0) is False
    assert client.revisions == before
    assert [q.keywords for q in store.load_plan().search.queries] == ["Go"]


def test_save_plan_if_version_without_preferences_writes_nothing(store, client):
    plan = compile_plan(SearchPreferences(keywords_include=["Rust"], version=1))
    assert store.save_plan_if_version(plan, 1) is False
    assert PLAN not in client.docs


def test_draft_round_trips(store, client):
    store.create_draft(draft())
    assert PreferenceDraft.model_validate(draft_doc(client)) == draft()


def test_apply_writes_preferences_plan_and_applied(store, client):
    seeded(store)
    store.create_draft(draft())
    later = NOW + timedelta(minutes=5)
    result = store.resolve_draft("d1", "42", "apply", later, compile_plan)
    assert result.status == "applied" and result.preferences_version == 2 and result.plan_kept is False
    prefs = store.load_preferences()
    assert prefs.version == 2 and prefs.updated_at == later and prefs.keywords_include == ["Rust"]
    assert store.load_plan() == result.plan and result.plan.preferences_version == 2
    assert draft_doc(client)["status"] == "APPLIED"


def test_second_apply_is_already_resolved(store):
    seeded(store)
    store.create_draft(draft())
    store.resolve_draft("d1", "42", "apply", NOW, compile_plan)
    result = store.resolve_draft("d1", "42", "apply", NOW, compile_plan)
    assert result.status == "already_resolved" and result.previous_status == "APPLIED"


def test_apply_after_another_draft_bumped_the_version_is_stale(store, client):
    seeded(store)
    store.create_draft(draft("d1", keywords=["Rust"]))
    store.create_draft(draft("d2", keywords=["Elixir"]))
    assert store.resolve_draft("d1", "42", "apply", NOW, compile_plan).status == "applied"
    before = client.docs[PREFS]
    result = store.resolve_draft("d2", "42", "apply", NOW, compile_plan)
    assert result.status == "stale"
    assert client.docs[PREFS] == before
    assert draft_doc(client, "d2")["status"] == "EXPIRED"


def test_expired_draft(store, client):
    seeded(store)
    store.create_draft(draft(expires_at=NOW))
    before = client.docs[PREFS]
    assert store.resolve_draft("d1", "42", "apply", NOW, compile_plan).status == "expired"
    assert draft_doc(client)["status"] == "EXPIRED" and client.docs[PREFS] == before


def test_cancel_writes_only_the_draft(store, client):
    seeded(store)
    store.create_draft(draft())
    before = {path: rev for path, rev in client.revisions.items() if path != "search_preference_drafts/d1"}
    result = store.resolve_draft("d1", "42", "cancel", NOW, compile_plan)
    assert result.status == "cancelled"
    assert draft_doc(client)["status"] == "CANCELLED"
    assert {p: r for p, r in client.revisions.items() if p != "search_preference_drafts/d1"} == before


def test_draft_of_another_chat_or_missing_is_not_found(store, client):
    seeded(store)
    store.create_draft(draft(chat_id="7"))
    assert store.resolve_draft("d1", "42", "apply", NOW, compile_plan).status == "not_found"
    assert store.resolve_draft("nope", "42", "apply", NOW, compile_plan).status == "not_found"
    assert draft_doc(client)["status"] == "PENDING"


def test_apply_without_keywords_keeps_previous_plan(store, client):
    seeded(store)
    plan_before, plan_rev = client.docs[PLAN], client.revisions[PLAN]
    store.create_draft(draft(keywords=[]))
    result = store.resolve_draft("d1", "42", "apply", NOW, compile_plan)
    assert result.status == "applied" and result.plan is None and result.plan_kept is True
    assert client.docs[PLAN] == plan_before and client.revisions[PLAN] == plan_rev
    assert store.load_preferences().version == 2


def test_concurrent_applies_on_same_base_one_applies_one_is_stale(store, client):
    seeded(store)
    store.create_draft(draft("d1", keywords=["Rust"]))
    store.create_draft(draft("d2", keywords=["Elixir"]))
    client.barrier = Barrier(2)
    client.barrier_on = lambda path: path == PREFS
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda d: store.resolve_draft(d, "42", "apply", NOW, compile_plan), ["d1", "d2"]))
    assert sorted(r.status for r in results) == ["applied", "stale"]
    assert client.conflicts == 1
    assert store.load_preferences().version == 2


def test_missing_or_invalid_documents_load_as_none(store, client):
    assert store.load_preferences() is None and store.load_plan() is None
    client.docs[PREFS] = {"posted_within_days": 99}
    client.docs[PLAN] = {"search": "broken"}
    assert store.load_preferences() is None and store.load_plan() is None
