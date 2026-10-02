from datetime import datetime, timedelta, timezone

from job_agent.domain.models import StoredProfile
from job_agent.domain.policies import (
    MatchingPolicy,
    SearchPreferences,
    job_key,
    profile_fingerprint,
    profile_is_current,
)


def test_job_key_uses_external_id_and_ignores_tracking(job):
    assert job_key(job) == job_key(job.model_copy(update={"url": "https://linkedin.com/jobs/view/123"}))
    no_id = job.model_copy(update={"external_id": ""})
    assert job_key(no_id) == job_key(no_id.model_copy(update={"url": job.url.split("?")[0]}))


def test_matching_policy(match):
    policy = MatchingPolicy(min_score_to_notify=70, min_score_to_tailor=80)
    assert policy.should_notify(match) and policy.should_tailor(match)
    assert not policy.should_tailor(match.model_copy(update={"resume_undersells": False}))
    assert not policy.should_tailor(match.model_copy(update={"score": 75}))
    assert not policy.should_notify(match.model_copy(update={"score": 50}))


def test_search_preferences_build_criteria(profile, job):
    prefs = SearchPreferences(locations=("Colombia",), extra_keywords=("django",), exclude_companies=("ACME & co",))
    criteria = prefs.criteria_for(profile, ["u1"])
    assert criteria.keywords == ["python backend", "django"]
    assert criteria.locations == ["Colombia"] and criteria.already_seen_urls == ["u1"]
    assert prefs.is_excluded(job.model_copy(update={"company": " acme & co "}))


def test_profile_freshness(profile):
    now = datetime.now(timezone.utc)
    fp = profile_fingerprint("cv", ["a1", "b2"])
    assert fp == profile_fingerprint("cv", ["b2", "a1"])
    stored = StoredProfile(profile=profile, fingerprint=fp, built_at=now - timedelta(days=3))
    assert profile_is_current(stored, fp, now, refresh_days=7)
    assert not profile_is_current(stored, fp, now, refresh_days=2)
    assert not profile_is_current(stored, profile_fingerprint("cv2", []), now, refresh_days=7)


def test_reuse_policy_only_offers_versions_of_current_base(profile):
    from job_agent.domain.models import ResumeVersion
    from job_agent.domain.policies import ReusePolicy, resume_fingerprint

    def v(i, fp, day):
        return ResumeVersion(id=f"v{i}", job_key="k", job_title="t", company="c", language="es", highlights=[],
                             base_fingerprint=fp, created_at=datetime(2026, 9, day, tzinfo=timezone.utc))

    current = resume_fingerprint("cv")
    assert current == resume_fingerprint("cv\n") != resume_fingerprint("cv v2")
    versions = [v(1, current, 1), v(2, "old", 2), v(3, current, 3), v(4, current, 2)]
    assert [x.id for x in ReusePolicy(max_candidates=2).candidates(versions, current)] == ["v3", "v4"]
    assert ReusePolicy(enabled=False).candidates(versions, current) == []
