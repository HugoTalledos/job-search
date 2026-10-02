"""LLM adapters against mocked HTTP transports (no API keys, no network)."""

import json
from datetime import datetime, timezone

import anthropic
import httpx
import httpx2
import pytest

from job_agent.adapters.llm import (
    AnthropicStructuredModel,
    LLMError,
    LlmJobMatcher,
    LlmResumeSelector,
    LlmResumeTailor,
    OpenRouterStructuredModel,
)
from job_agent.adapters.llm.anthropic_model import FALLBACK_BETA
from job_agent.adapters.llm.openrouter_model import strict_json_schema
from job_agent.domain.models import JobMatch, ResumeVersion, ReuseDecision, TailoredResume

# --- Anthropic ----------------------------------------------------------------------------------


def _anthropic(payload, seen):
    def handler(request):
        seen.append((json.loads(request.content), request.headers.get("anthropic-beta")))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": payload}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })

    client = anthropic.Anthropic(api_key="x", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return AnthropicStructuredModel(client=client)


def test_anthropic_matcher_request_shape(job, profile, match):
    seen = []
    assert LlmJobMatcher(_anthropic(match.model_dump_json(), seen)).score(job, profile, "cv") == match
    body, beta = seen[0]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    assert FALLBACK_BETA in beta
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_selector_and_tailor(job, match, profile, tailored):
    seen = []
    decision = ReuseDecision(action="reuse", version_id="output/v1", covered_requirements=["Python"],
                             missing_requirements=[], rationale="ok")
    version = ResumeVersion(id="output/v1", job_key="k", job_title="Backend Dev", company="Globex", language="en",
                            highlights=["Python"], base_fingerprint="f", created_at=datetime.now(timezone.utc))
    assert LlmResumeSelector(_anthropic(decision.model_dump_json(), seen)).choose(job, match, [version]) == decision
    assert '"id": "output/v1"' in seen[0][0]["messages"][0]["content"][0]["text"]

    seen.clear()
    tailor = LlmResumeTailor(_anthropic(tailored.model_dump_json(), seen))
    tailor.tailor(job, match, profile, "cv")
    tailor.tailor(job, match, profile, "cv", starting_from="# Previous")
    texts = [[b["text"] for b in body["messages"][0]["content"]] for body, _ in seen]
    assert not any("<starting_version>" in t for t in texts[0])
    assert any("<starting_version>\n# Previous" in t for t in texts[1])


# --- OpenRouter ---------------------------------------------------------------------------------


def _completion(content, finish="stop"):
    return {"id": "gen-1", "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]}


def _openrouter(responses, seen, **kwargs):
    """``responses``: list of (status, json) returned in order."""
    queue = list(responses)

    def handler(request):
        seen.append(json.loads(request.content))
        assert request.url == "https://openrouter.ai/api/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer or-key"
        status, payload = queue.pop(0)
        return httpx.Response(status, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenRouterStructuredModel("anthropic/some-model", "or-key", http_client=client, **kwargs)


def test_openrouter_json_schema_request(job, profile, match):
    seen = []
    model = _openrouter([(200, _completion(match.model_dump_json()))], seen)
    assert LlmJobMatcher(model).score(job, profile, "cv") == match
    body = seen[0]
    assert body["model"] == "anthropic/some-model" and body["max_tokens"] == 8000
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    assert "reasoning" not in body
    assert body["messages"][0]["role"] == "system" and "evaluate how well" in body["messages"][0]["content"]
    user_parts = body["messages"][1]["content"]
    assert user_parts[0]["cache_control"] == {"type": "ephemeral"} and "<job>" in user_parts[1]["text"]


def test_openrouter_falls_back_to_prompt_schema_when_unsupported(job, profile, match):
    seen = []
    model = _openrouter([
        (404, {"error": {"message": "No endpoints found that can handle the requested parameters."}}),
        (200, _completion("```json\n" + match.model_dump_json() + "\n```")),
        (200, _completion(match.model_dump_json())),
    ], seen, reasoning=True)
    matcher = LlmJobMatcher(model)
    assert matcher.score(job, profile, "cv") == match
    assert matcher.score(job, profile, "cv") == match  # remembered: no second json_schema attempt
    assert "response_format" in seen[0]
    for body in seen[1:]:
        assert "response_format" not in body and "provider" not in body
        assert "conforms to this JSON schema" in body["messages"][0]["content"]
        assert body["reasoning"] == {"effort": "low"}
    assert len(seen) == 3


def test_openrouter_retries_invalid_answer_once(job, profile, match):
    seen = []
    model = _openrouter([(200, _completion('{"score": "high"}')), (200, _completion(match.model_dump_json()))], seen)
    assert LlmJobMatcher(model).score(job, profile, "cv") == match
    assert seen[1]["messages"][-1]["content"].startswith("That answer is not valid")
    assert seen[1]["messages"][-2] == {"role": "assistant", "content": '{"score": "high"}'}

    bad = _openrouter([(200, _completion("nope")), (200, _completion("still nope"))], [])
    with pytest.raises(LLMError, match="did not return valid JobMatch"):
        LlmJobMatcher(bad).score(job, profile, "cv")


def test_openrouter_errors(monkeypatch, job, profile, match):
    monkeypatch.setattr("job_agent.adapters.llm.openrouter_model.time.sleep", lambda s: None)
    seen = []
    model = _openrouter([(429, {"error": "rate"}), (200, _completion(match.model_dump_json()))], seen)
    assert LlmJobMatcher(model).score(job, profile, "cv") == match and len(seen) == 2

    with pytest.raises(LLMError, match="truncated"):
        LlmJobMatcher(_openrouter([(200, _completion("{", "length"))], [])).score(job, profile, "cv")
    with pytest.raises(LLMError, match="HTTP 401"):
        LlmJobMatcher(_openrouter([(401, {"error": "bad key"})], [])).score(job, profile, "cv")
    with pytest.raises(LLMError, match="error"):
        LlmJobMatcher(_openrouter([(200, {"error": {"message": "upstream"}})], [])).score(job, profile, "cv")
    with pytest.raises(ValueError, match="model id"):
        OpenRouterStructuredModel("", "k")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterStructuredModel("m", "")


def test_strict_schema_closes_every_object():
    schema = strict_json_schema(TailoredResume)
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    change = schema["$defs"]["ResumeChange"]
    assert change["additionalProperties"] is False and set(change["required"]) == set(change["properties"])
    assert strict_json_schema(JobMatch)["properties"]["verdict"]["enum"] == ["strong", "good", "weak", "no"]
