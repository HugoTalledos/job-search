"""Exercise the Claude adapters against a mocked HTTP transport (no API key needed)."""

import json

import anthropic
import httpx2

from job_agent.adapters.llm import ClaudeJobMatcher, ClaudeResumeSelector, ClaudeResumeTailor, claude_client


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": claude_client.MODEL, "content": content,
        "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def test_matcher_request_shape(monkeypatch, job, profile, match):
    seen = {}

    def handler(request):
        seen["body"], seen["beta"] = json.loads(request.content), request.headers.get("anthropic-beta")
        return httpx2.Response(200, json=_message([{"type": "text", "text": match.model_dump_json()}]))

    fake = anthropic.Anthropic(api_key="x", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(claude_client, "_client", fake)
    assert ClaudeJobMatcher().score(job, profile, "cv") == match
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    assert claude_client.FALLBACK_BETA in seen["beta"]


def _capture_structured(monkeypatch, payload):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=_message([{"type": "text", "text": payload}]))

    fake = anthropic.Anthropic(api_key="x", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(claude_client, "_client", fake)
    return seen


def test_selector_sends_library_and_parses_decision(monkeypatch, job, match):
    from datetime import datetime, timezone

    from job_agent.domain.models import ResumeVersion, ReuseDecision

    decision = ReuseDecision(action="reuse", version_id="output/v1", covered_requirements=["Python"],
                             missing_requirements=[], rationale="ok")
    seen = _capture_structured(monkeypatch, decision.model_dump_json())
    version = ResumeVersion(id="output/v1", job_key="k", job_title="Backend Dev", company="Globex", language="en",
                            highlights=["Python"], base_fingerprint="f", created_at=datetime.now(timezone.utc))
    assert ClaudeResumeSelector().choose(job, match, [version]) == decision
    prompt = seen[0]["messages"][0]["content"]
    assert '"id": "output/v1"' in prompt and "Backend Dev @ Globex" in prompt


def test_tailor_includes_starting_version_only_when_adapting(monkeypatch, job, match, profile, tailored):
    seen = _capture_structured(monkeypatch, tailored.model_dump_json())
    tailor = ClaudeResumeTailor()
    assert tailor.tailor(job, match, profile, "cv") == tailored
    assert tailor.tailor(job, match, profile, "cv", starting_from="# Previous") == tailored
    blocks = [[b["text"] for b in call["messages"][0]["content"]] for call in seen]
    assert not any("<starting_version>" in t for t in blocks[0])
    assert any("<starting_version>\n# Previous" in t for t in blocks[1])
