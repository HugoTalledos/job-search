import pytest
from fastapi.testclient import TestClient

from job_agent.entrypoints.http import create_app


class Reader:
    def __init__(self, result=None, error=None):
        self.result = [] if result is None else result
        self.error = error
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.mark.parametrize("count", [0, 1])
def test_webhook_returns_empty_200_after_read(count, job):
    reader = Reader([job] * count)

    response = TestClient(create_app(reader, "secret-key")).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 200
    assert response.content == b""
    assert reader.calls == 1


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_webhook_rejects_unauthorized_without_read(headers):
    reader = Reader()

    response = TestClient(create_app(reader, "secret-key")).post("/webhooks/inference", headers=headers)

    assert response.status_code == 401
    assert reader.calls == 0


def test_get_does_not_start_read():
    reader = Reader()

    response = TestClient(create_app(reader, "secret-key")).get(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 405
    assert reader.calls == 0


def test_read_failure_returns_500_without_sensitive_details():
    reader = Reader(error=RuntimeError("SECRET-JOB-DESCRIPTION"))

    response = TestClient(create_app(reader, "secret-key")).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 500
    assert b"SECRET-JOB-DESCRIPTION" not in response.content
    assert b"secret-key" not in response.content


def test_service_rejects_blank_configured_api_key():
    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        create_app(Reader(), " ")


def test_non_ascii_header_is_unauthorized_without_read():
    reader = Reader()

    response = TestClient(create_app(reader, "secret-key")).post(
        "/webhooks/inference", headers={b"X-API-Key": b"\xff"}
    )

    assert response.status_code == 401
    assert reader.calls == 0


def test_utf8_api_key_authenticates():
    reader = Reader()

    response = TestClient(create_app(reader, "contraseña")).post(
        "/webhooks/inference", headers={b"X-API-Key": "contraseña".encode("utf-8")}
    )

    assert response.status_code == 200
    assert reader.calls == 1
