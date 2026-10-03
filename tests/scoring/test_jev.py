import json

import httpx
import pytest

from job_agent.scoring.jev import JevScoringTool
from job_agent.scoring.models import ScoreResult


def answer(score=2.5, confidence=0.72, kind="score"):
    return {"answers": {"affinity": {"type": kind, "score": score, "confidence": confidence}}}


def tool_for(payload, seen, status=200):
    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    return JevScoringTool("or-key", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_jev_sends_profile_and_job_to_decisions_api(profile, job):
    seen = []

    result = tool_for(answer(), seen).score(profile, job)

    assert result == ScoreResult(score=63, confidence=0.72, model="typesafe/jev-1.13")
    [request] = seen
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
    assert request.headers["authorization"] == "Bearer or-key"
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"profile": profile.model_dump(), "job": job.model_dump()}
    assert list(body["questions"]) == ["affinity"]
    question = body["questions"]["affinity"]
    assert question["type"] == "score"
    assert len(question["criteria"]) == 5
    assert "requisitos" in question["instructions"].lower()
    assert "requisito indispensable" in question["criteria"][0]
    assert "evidencia" in question["criteria"][4]


@pytest.mark.parametrize("payload", [
    {"answers": {}},
    answer(kind="choice"),
    answer(score=4.5),
    answer(score="high"),
    answer(score="2.5"),
    answer(confidence=1.2),
    answer(confidence="0.7"),
])
def test_jev_rejects_invalid_answer(profile, job, payload):
    with pytest.raises(ValueError):
        tool_for(payload, []).score(profile, job)


def test_jev_propagates_http_failure(profile, job):
    with pytest.raises(httpx.HTTPStatusError):
        tool_for({"error": "unavailable"}, [], status=503).score(profile, job)


def test_jev_requires_api_key():
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        JevScoringTool(" ")
