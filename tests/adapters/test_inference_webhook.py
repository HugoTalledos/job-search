import httpx
import pytest

from local_collector.adapters.inference_webhook import InferenceWebhookTrigger
from local_collector.bootstrap import build_inference_trigger


def _trigger(handler, base_url="https://agent.example.run.app", api_key="KEY-123"):
    return InferenceWebhookTrigger(base_url, api_key, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_trigger_posts_to_inference_path_with_api_key_header():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    _trigger(handler, base_url="https://agent.example.run.app/ ").trigger()

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "https://agent.example.run.app/webhooks/inference"
    assert request.headers["X-API-Key"] == "KEY-123"
    assert "KEY-123" not in str(request.url)


def test_trigger_explains_rejected_api_key():
    with pytest.raises(RuntimeError, match="401"):
        _trigger(lambda request: httpx.Response(401)).trigger()


def test_trigger_fails_on_unexpected_status():
    with pytest.raises(RuntimeError, match="503"):
        _trigger(lambda request: httpx.Response(503)).trigger()


def test_trigger_reports_connection_error_without_api_key():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RuntimeError, match="ConnectError") as error:
        _trigger(handler).trigger()

    assert "KEY-123" not in str(error.value)


@pytest.mark.parametrize("base_url", ["http://agent.example.com", "ftp://agent.example.com", "agent.example.com",
                                      "https://agent.example.com?key=x"])
def test_trigger_rejects_insecure_or_invalid_urls(base_url):
    with pytest.raises(ValueError, match="JOB_AGENT_URL"):
        InferenceWebhookTrigger(base_url, "KEY-123")


def test_trigger_allows_plain_http_on_localhost():
    trigger = InferenceWebhookTrigger("http://127.0.0.1:8000", "KEY-123")
    assert trigger.url == "http://127.0.0.1:8000/webhooks/inference"


def test_trigger_requires_api_key():
    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        InferenceWebhookTrigger("https://agent.example.com", " ")


def test_build_inference_trigger_is_optional_without_url():
    assert build_inference_trigger({"JOB_AGENT_WEBHOOK_API_KEY": "KEY-123"}) is None


def test_build_inference_trigger_requires_api_key_when_url_is_set():
    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        build_inference_trigger({"JOB_AGENT_URL": "https://agent.example.com"})


def test_build_inference_trigger_uses_environment():
    trigger = build_inference_trigger({"JOB_AGENT_URL": "https://agent.example.com",
                                       "JOB_AGENT_WEBHOOK_API_KEY": "KEY-123"})
    assert trigger.url == "https://agent.example.com/webhooks/inference"


class CountingRunner:
    def __init__(self):
        self.runs = 0

    def execute(self):
        self.runs += 1


@pytest.mark.parametrize("supplied_key, runs", [("KEY-123", 1), ("WRONG", 0)])
def test_trigger_matches_the_agent_inference_endpoint(supplied_key, runs):
    from fastapi.testclient import TestClient

    from job_agent.entrypoints.http import create_app

    runner = CountingRunner()
    client = TestClient(create_app(runner, "KEY-123"))
    trigger = InferenceWebhookTrigger("https://testserver", supplied_key, client=client)

    if runs:
        trigger.trigger()
    else:
        with pytest.raises(RuntimeError, match="401"):
            trigger.trigger()

    assert runner.runs == runs
