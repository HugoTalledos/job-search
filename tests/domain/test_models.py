import json

import pytest
from pydantic import ValidationError

from job_agent.domain.models import JobMatch, JobRequirement


def test_old_job_match_json_validates_without_requirements(match):
    payload = match.model_dump(exclude={"requirements"})

    restored = JobMatch.model_validate_json(json.dumps(payload))

    assert restored.requirements == []
    assert restored.score == 82
    assert restored.reasons == ["Python avanzado"]
    assert restored.gaps == ["Kubernetes"]


def test_match_preserves_requirement_evidence(match):
    payload = match.model_dump()
    payload["requirements"] = [
        {"name": "Python", "priority": "must", "covered": True, "evidence": "CV: backend en Python"},
        {"name": "Kubernetes", "priority": "nice", "covered": False, "evidence": "Sin evidencia en CV o perfil"},
    ]

    restored = JobMatch.model_validate_json(json.dumps(payload))

    assert [(r.name, r.priority, r.covered) for r in restored.requirements] == [
        ("Python", "must", True), ("Kubernetes", "nice", False),
    ]
    assert restored.requirements[0].evidence == "CV: backend en Python"
    assert restored.requirements[1].evidence == "Sin evidencia en CV o perfil"
    assert restored.model_dump()["requirements"] == payload["requirements"]


def test_requirement_priority_rejects_unknown_classification():
    with pytest.raises(ValidationError):
        JobRequirement(name="Python", priority="optional", covered=True, evidence="CV")


def test_default_requirements_are_independent(match):
    other = JobMatch.model_validate(match.model_dump(exclude={"requirements"}))
    match.requirements.append(JobRequirement(name="Python", priority="must", covered=True, evidence="CV"))

    assert other.requirements == []
