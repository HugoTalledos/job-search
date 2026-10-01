"""Exercise the real SDK code paths against a mocked HTTP transport (no API key needed)."""
import asyncio
import json

import anthropic
import httpx2 as httpx

from job_agent import llm
from job_agent.config import SearchConfig
from job_agent.models import JobMatch
from job_agent.sources import search_agent


def _message(content, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": llm.MODEL, "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def test_structured_request_shape(monkeypatch, match):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(200, json=_message([{"type": "text", "text": match.model_dump_json()}]))

    fake = anthropic.Anthropic(api_key="x", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm, "_client", fake)
    out = llm.structured(system="s", content="c", schema=JobMatch, effort="low")
    assert out == match
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    assert llm.FALLBACK_BETA in seen["beta"]


def test_search_agent_loop_collects_submitted_jobs(monkeypatch, profile, job):
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(200, json=_message(
                [{"type": "tool_use", "id": "tu_1", "name": "submit_jobs", "input": {"jobs": [job.model_dump()]}}],
                stop_reason="tool_use",
            ))
        return httpx.Response(200, json=_message([{"type": "text", "text": "done"}]))

    fake = anthropic.AsyncAnthropic(api_key="x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm, "_async_client", fake)
    cfg = SearchConfig(web_search=True, mcp_servers=[])
    jobs = asyncio.run(search_agent.search_jobs(profile, cfg, ["https://seen"]))
    assert jobs == [job]
    assert len(calls) == 2
    tool_names = [t.get("name") for t in calls[0]["tools"]]
    assert "submit_jobs" in tool_names and "web_search" in tool_names
    assert calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert "https://seen" in calls[0]["messages"][0]["content"]
