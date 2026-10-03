import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from job_agent.entrypoints.http import create_app


class Runner:
    def __init__(self, result=None, error=None):
        self.result = [] if result is None else result
        self.error = error
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def test_webhook_returns_empty_200_after_scheduling():
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 200
    assert response.content == b""
    assert runner.calls == 1


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_webhook_rejects_unauthorized_without_work(headers):
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).post("/webhooks/inference", headers=headers)

    assert response.status_code == 401
    assert runner.calls == 0


def test_get_does_not_start_work():
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).get(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 405
    assert runner.calls == 0


def test_background_failure_keeps_200_and_logs_no_sensitive_details(caplog):
    runner = Runner(error=RuntimeError("SECRET-JOB-DESCRIPTION"))

    with caplog.at_level(logging.ERROR):
        response = TestClient(create_app(runner, "secret-key")).post(
            "/webhooks/inference", headers={"X-API-Key": "secret-key"}
        )

    assert response.status_code == 200
    assert response.content == b""
    assert b"SECRET-JOB-DESCRIPTION" not in response.content
    assert b"secret-key" not in response.content
    assert "SECRET-JOB-DESCRIPTION" not in caplog.text
    assert "secret-key" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_service_rejects_blank_configured_api_key():
    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        create_app(Runner(), " ")


def test_non_ascii_header_is_unauthorized_without_work():
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).post(
        "/webhooks/inference", headers={b"X-API-Key": b"\xff"}
    )

    assert response.status_code == 401
    assert runner.calls == 0


def test_utf8_api_key_authenticates():
    runner = Runner()

    response = TestClient(create_app(runner, "contraseña")).post(
        "/webhooks/inference", headers={b"X-API-Key": "contraseña".encode("utf-8")}
    )

    assert response.status_code == 200
    assert runner.calls == 1


def test_response_body_is_sent_before_background_scoring_finishes():
    release = threading.Event()

    class BlockingRunner:
        finished = False

        def execute(self):
            release.wait(timeout=3)
            self.finished = True

    runner = BlockingRunner()
    app = create_app(runner, "secret-key")
    body_sent = asyncio.Event()

    async def run_request():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                body_sent.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "method": "POST",
            "path": "/webhooks/inference", "raw_path": b"/webhooks/inference",
            "root_path": "", "query_string": b"", "headers": [(b"x-api-key", b"secret-key")],
            "http_version": "1.1", "scheme": "http", "server": ("test", 80), "client": ("test", 123),
        }
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(body_sent.wait(), timeout=2)
            assert runner.finished is False
        finally:
            release.set()
        await asyncio.wait_for(task, timeout=2)
        assert runner.finished is True

    asyncio.run(run_request())


def test_background_runs_are_serialized_within_one_app():
    first_started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    class BlockingRunner:
        calls = 0

        def execute(self):
            self.calls += 1
            if self.calls == 1:
                first_started.set()
                release.wait(timeout=3)
            else:
                second_started.set()

    runner = BlockingRunner()
    app = create_app(runner, "secret-key")

    def post():
        return TestClient(app).post("/webhooks/inference", headers={"X-API-Key": "secret-key"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post)
        assert first_started.wait(timeout=2)
        second = pool.submit(post)
        try:
            assert not second_started.wait(timeout=0.2)
        finally:
            release.set()
        assert first.result(timeout=2).status_code == 200
        assert second.result(timeout=2).status_code == 200
    assert runner.calls == 2
