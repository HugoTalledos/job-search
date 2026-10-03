import hashlib
import json
from datetime import datetime, timezone

from job_contracts import SearchPreferences
from job_agent.domain.policies import SearchBudgets, build_search_plan, profile_fingerprint

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def test_plan_orders_keywords_and_cuts_queries(profile):
    prefs = SearchPreferences(keywords_include=["Django"], keywords_exclude=["frontend"], locations=["Remote", "España"], version=4)
    build = build_search_plan(prefs, profile, SearchBudgets(max_roles_from_profile=1, max_queries=3), NOW)
    assert [(q.keywords, q.location) for q in build.plan.search.queries] == [("Django", "Remote"), ("Django", "España"), (profile.target_roles[0], "Remote")]
    assert build.total_queries > 3 and build.plan.preferences_version == 4


def test_only_my_keywords_ignores_profile(profile):
    prefs = SearchPreferences(keywords_include=["Rust"], use_profile_keywords=False)
    assert {q.keywords for q in build_search_plan(prefs, profile, SearchBudgets(), NOW).plan.search.queries} == {"Rust"}


def test_no_keywords_builds_no_plan():
    assert build_search_plan(SearchPreferences(), None, SearchBudgets(), NOW).plan is None


def test_same_inputs_same_plan(profile):
    prefs = SearchPreferences(keywords_include=["Go"])
    assert build_search_plan(prefs, profile, SearchBudgets(), NOW) == build_search_plan(prefs, profile, SearchBudgets(), NOW)


def test_profile_keywords_drop_excluded_blank_and_duplicate_entries(profile):
    profile = profile.model_copy(update={
        "target_roles": ["Backend Engineer", "Data Engineer", "ML Engineer", "Ignored Fourth"],
        "search_keywords": ["  ", "backend-engineer", "Python Backend", "django"],
    })
    prefs = SearchPreferences(keywords_include=["Django"], keywords_exclude=["data engineer", "PYTHON backend"])
    build = build_search_plan(prefs, profile, SearchBudgets(), NOW)
    assert [q.keywords for q in build.plan.search.queries] == ["Django", "Backend Engineer", "ML Engineer"]


def test_locations_fall_back_to_profile_then_to_none(profile):
    prefs = SearchPreferences(keywords_include=["Go"], use_profile_keywords=False)
    assert [q.location for q in build_search_plan(prefs, profile, SearchBudgets(), NOW).plan.search.queries] == ["Remote"]
    no_locations = profile.model_copy(update={"locations": []})
    assert [q.location for q in build_search_plan(prefs, no_locations, SearchBudgets(), NOW).plan.search.queries] == [None]
    build = build_search_plan(prefs, None, SearchBudgets(), NOW)
    assert [q.location for q in build.plan.search.queries] == [None]
    assert build.plan.profile_fingerprint == ""


def test_plan_copies_filters_exclusions_budgets_and_metadata(profile):
    prefs = SearchPreferences(
        keywords_include=["Go"], posted_within_days=7, work_types=["remote", "hybrid"],
        experience_levels=["mid_senior"], exclude_companies=["Acme Inc."], exclude_title_keywords=["Senior"],
        version=9,
    )
    build = build_search_plan(prefs, profile, SearchBudgets(max_details_per_run=11), NOW)
    plan = build.plan
    assert plan.search.posted_within_days == 7
    assert plan.search.work_types == ["remote", "hybrid"]
    assert plan.search.experience_levels == ["mid_senior"]
    assert plan.exclude_companies == ["Acme Inc."]
    assert plan.exclude_title_keywords == ["Senior"]
    assert plan.max_details_per_run == 11
    assert plan.preferences_version == 9
    assert plan.profile_fingerprint == profile_fingerprint(profile)
    assert plan.built_at == NOW
    assert build.total_queries == len(plan.search.queries) == 3


def test_profile_fingerprint_is_sha256_of_sorted_json(profile):
    expected = hashlib.sha256(json.dumps(profile.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    assert profile_fingerprint(profile) == expected
    assert profile_fingerprint(None) == ""
    assert profile_fingerprint(profile.model_copy(update={"locations": ["Bogotá"]})) != expected
