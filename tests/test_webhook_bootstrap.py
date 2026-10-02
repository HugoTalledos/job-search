import importlib

import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore


class EmptyCollection:
    def stream(self):
        return iter(())


class FakeFirestoreClient:
    def collection(self, name):
        assert name == "job_postings"
        return EmptyCollection()


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_WEBHOOK_API_KEY", "fixture-key")
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "fixture-project")
    monkeypatch.setattr(firestore, "Client", lambda **kwargs: FakeFirestoreClient())
    module = importlib.import_module("job_agent.webhook")
    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    return module


def test_webhook_requires_api_key_at_startup(webhook, monkeypatch):
    monkeypatch.delenv("JOB_AGENT_WEBHOOK_API_KEY")

    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        webhook.build_webhook_app()


def test_webhook_requires_firestore_project_at_startup(webhook, monkeypatch):
    monkeypatch.delenv("FIRESTORE_PROJECT_ID")

    with pytest.raises(ValueError, match="FIRESTORE_PROJECT_ID"):
        webhook.build_webhook_app()


def test_webhook_wires_firestore_without_llm(webhook, monkeypatch):
    projects = []
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: projects.append(kwargs["project"]) or FakeFirestoreClient())

    app = webhook.build_webhook_app()
    response = TestClient(app).post("/webhooks/inference", headers={"X-API-Key": "fixture-key"})

    assert projects == ["fixture-project"]
    assert response.status_code == 200
    assert response.content == b""


def test_webhook_exposes_firestore_credential_failure_at_startup(webhook, monkeypatch):
    def missing_credentials(**kwargs):
        raise ValueError("credentials missing")

    monkeypatch.setattr(webhook.firestore, "Client", missing_credentials)

    with pytest.raises(ValueError, match="credentials missing"):
        webhook.build_webhook_app()
