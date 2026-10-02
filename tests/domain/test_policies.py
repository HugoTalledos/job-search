from datetime import datetime, timedelta, timezone

from job_agent.domain.models import StoredProfile
from job_agent.domain.policies import (
    MatchingPolicy,
    SearchPreferences,
    job_key,
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


def test_search_plan_is_keywords_by_location(profile):
    prefs = SearchPreferences(locations=("Colombia", "Remote"), extra_keywords=("Django", "backend engineer"), max_queries=5)
    plan = prefs.plan_for(profile)
    assert [(q.keywords, q.location) for q in plan.queries] == [
        ("Django", "Colombia"), ("Django", "Remote"),
        ("backend engineer", "Colombia"), ("backend engineer", "Remote"),  # duplicates "Backend Engineer" role
        ("python backend", "Colombia"),
    ]
    assert plan.posted_within_days == 1


def test_normalisation_and_duplicate_signature(job):
    from job_agent.domain.policies import duplicate_signature, normalize_company, normalize_title

    assert normalize_company("Globant S.A.S.") == normalize_company("GLOBANT") == "globant"
    assert normalize_title("Sr. Backend Engineer (Remote)") == "senior back end engineer"
    a = job.model_copy(update={"title": "Desarrollador Backend Sr", "company": "Acme Inc."})
    b = job.model_copy(update={"title": "Sr. Backend Developer - Remoto", "company": "ACME"})
    assert duplicate_signature(a) == duplicate_signature(b)
    assert duplicate_signature(job.model_copy(update={"company": ""})) == ""  # unknown: never a duplicate


def test_job_filter(job):
    from job_agent.domain.policies import JobFilter

    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    f = JobFilter(exclude_companies=("acme & co",), exclude_title_keywords=("Intern", "lead"),
                  posted_within_days=1, work_types=("remote", "hybrid"))
    ok = job.model_copy(update={"company": "Globex", "posted_at": "2026-10-01T10:00:00+00:00"})
    assert f.rejection(ok, now) is None
    assert f.rejection(ok.model_copy(update={"company": "ACME & Co."}), now) == "empresa_excluida"
    assert f.rejection(ok.model_copy(update={"title": "Backend Intern"}), now) == "palabra_excluida_en_titulo"
    assert f.rejection(ok.model_copy(update={"title": "Leadership Coach"}), now) is None  # whole words only
    assert f.rejection(ok.model_copy(update={"remote": "onsite"}), now) == "modalidad_no_deseada"
    assert f.rejection(ok.model_copy(update={"remote": "unknown"}), now) is None
    assert f.rejection(ok.model_copy(update={"posted_at": "2026-09-20T00:00:00+00:00"}), now) == "publicacion_antigua"
    assert f.rejection(ok.model_copy(update={"posted_at": ""}), now) is None
    assert f.rejection(ok.model_copy(update={"description": " "}), now) == "sin_descripcion"


def test_profile_refresh_policy(profile):
    from job_agent.domain.policies import ProfileRefreshPolicy, repos_fingerprint, resume_fingerprint

    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert repos_fingerprint(["a1", "b2"]) == repos_fingerprint(["b2", "a1"]) != repos_fingerprint(["a1"])
    policy = ProfileRefreshPolicy(refresh_days=30)
    stored = StoredProfile(profile=profile, resume_fingerprint=resume_fingerprint("cv"), repos_fingerprint="r1",
                           built_at=now - timedelta(days=10))
    assert policy.rebuild_reason(None, "x", None, now) == "no hay perfil guardado"
    assert policy.rebuild_reason(stored, resume_fingerprint("cv"), "r1", now) is None
    assert "hoja de vida" in policy.rebuild_reason(stored, resume_fingerprint("cv2"), "r1", now)
    assert policy.rebuild_reason(stored, resume_fingerprint("cv"), "r2", now) is None  # repos changed, too soon
    old = stored.model_copy(update={"built_at": now - timedelta(days=30)})
    assert "repositorios" in policy.rebuild_reason(old, resume_fingerprint("cv"), "r2", now)
    assert policy.rebuild_reason(old, resume_fingerprint("cv"), "r1", now) is None  # due, but nothing changed
    checked = old.model_copy(update={"repos_checked_at": now - timedelta(days=1)})
    assert not policy.repos_check_due(checked, now) and policy.repos_check_due(old, now)


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
