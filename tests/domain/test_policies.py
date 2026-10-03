from datetime import datetime, timedelta, timezone

from job_agent.domain.models import Skill, StoredProfile
from job_agent.domain.policies import (
    profile_changes,
    resume_fingerprint,
)


def test_normalisation():
    from job_agent.domain.policies import normalize_company, normalize_title

    assert normalize_company("Globant S.A.S.") == normalize_company("GLOBANT") == "globant"
    assert normalize_title("Sr. Backend Engineer (Remote)") == "senior back end engineer"


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


def test_resume_fingerprint_ignores_surrounding_whitespace():
    assert resume_fingerprint("cv") == resume_fingerprint("cv\n") != resume_fingerprint("cv v2")


def test_profile_changes_are_empty_for_a_first_profile(profile):
    assert profile_changes(None, profile) == []
    assert profile_changes(profile, profile) == []


def test_profile_changes_list_what_the_rebuilt_profile_brings(profile):
    from job_agent.domain.models import Skill
    rebuilt = profile.model_copy(update={
        "seniority": "senior",
        "years_of_experience": 5,
        "skills": [Skill(name="python", level="expert", evidence="repo"), Skill(name="Go", level="basic", evidence="r")],
        "target_roles": ["backend engineer", "Platform Engineer"],
        "strengths_missing_from_resume": ["AWS Lambda", "Terraform"],
    })

    assert profile_changes(profile, rebuilt) == [
        "Seniority: mid → senior",
        "Años de experiencia: 4 → 5",
        "Habilidades nuevas: Go (básico)",
        "Cambio de nivel: python (avanzado → experto)",
        "Cargos objetivo nuevos: Platform Engineer",
        "Fortalezas nuevas que tu CV no muestra: Terraform",
    ]


def test_profile_changes_mention_removed_skills(profile):
    assert profile_changes(profile, profile.model_copy(update={"skills": []})) == [
        "Habilidades que ya no aparecen: Python"
    ]
