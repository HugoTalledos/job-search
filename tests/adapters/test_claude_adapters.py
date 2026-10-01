"""Exercise the Claude adapters against a mocked HTTP transport (no API key needed)."""

import json

import anthropic
import httpx2

from job_agent.adapters.job_sources import McpJobSource, WebSearchJobSource
from job_agent.adapters.llm import ClaudeJobMatcher, claude_client
from job_agent.application.ports import JobSource
from job_agent.domain.policies import SearchPreferences
from tests.adapters.test_mcp_job_source import FAKE


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


def _scripted_agent(monkeypatch, job):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx2.Response(200, json=_message(
                [{"type": "tool_use", "id": "tu_1", "name": "submit_jobs", "input": {"jobs": [job.model_dump()]}}],
                stop_reason="tool_use",
            ))
        return httpx2.Response(200, json=_message([{"type": "text", "text": "done"}]))

    fake = anthropic.AsyncAnthropic(api_key="x", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(claude_client, "_async_client", fake)
    return calls


def test_web_search_source_runs_agent_and_collects_jobs(monkeypatch, profile, job):
    calls = _scripted_agent(monkeypatch, job)
    source = WebSearchJobSource(["linkedin.com"])
    assert isinstance(source, JobSource)
    jobs = source.search(SearchPreferences().criteria_for(profile, ["https://seen"]))
    assert jobs == [job] and len(calls) == 2
    web = next(t for t in calls[0]["tools"] if t.get("name") == "web_search")
    assert web["allowed_domains"] == ["linkedin.com"]
    assert calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert "https://seen" in calls[0]["messages"][0]["content"]


def test_mcp_source_hands_server_tools_to_agent(monkeypatch, profile, job):
    calls = _scripted_agent(monkeypatch, job)
    source = McpJobSource(FAKE)
    assert source.search(SearchPreferences().criteria_for(profile, [])) == [job]
    assert {t["name"] for t in calls[0]["tools"]} == {"submit_jobs", "search_jobs"}
