import pytest
from pydantic import ValidationError

from job_contracts import SearchPreferences
from job_contracts.models import CollectionReport, CollectorPlan
from job_contracts.normalize import exclusion_reason


def test_preferences_canonicalise_lists():
    prefs = SearchPreferences(keywords_include=[" Python ", "python", ""], exclude_companies=["Acme Inc.", "ACME"])
    assert prefs.keywords_include == ["Python"]
    assert prefs.exclude_companies == ["Acme Inc."]


@pytest.mark.parametrize(
    "bad",
    [{"work_types": ["office"]}, {"experience_levels": ["senior"]}, {"posted_within_days": 0}, {"posted_within_days": 31}],
)
def test_preferences_reject_values_linkedin_does_not_accept(bad):
    with pytest.raises(ValidationError):
        SearchPreferences(**bad)


def test_content_equals_ignores_version_and_timestamp():
    a = SearchPreferences(keywords_include=["x"], version=1)
    assert a.content_equals(SearchPreferences(keywords_include=["x"], version=7))
    assert not a.content_equals(SearchPreferences(keywords_include=["y"]))


def test_old_plan_without_exclusions_still_loads():
    plan = CollectorPlan.model_validate(
        {"search": {"queries": [{"keywords": "backend"}], "posted_within_days": 2}, "max_details_per_run": 5}
    )
    assert plan.exclude_companies == [] and plan.preferences_version == 0
    assert plan.profile_fingerprint == "" and plan.built_at is None
    assert CollectionReport().excluded == 0


def test_exclusion_reason(job):
    assert exclusion_reason(job, [], []) is None
    assert exclusion_reason(job, ["ACME"], []) == "empresa_excluida"
    assert exclusion_reason(job, [], ["engineer"]) == "palabra_excluida_en_titulo"
    assert exclusion_reason(job, [], ["engine"]) is None  # whole words only
