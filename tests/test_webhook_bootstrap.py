import importlib

import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore

from job_agent.scoring.models import ScoreResult


class Snapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self.data = data

    def to_dict(self):
        return self.data


class Document:
    def __init__(self, client, collection, doc_id):
        self.client, self.collection, self.id = client, collection, doc_id

    def get(self):
        return Snapshot(self.id, self.client.docs.get(f"{self.collection}/{self.id}"))

    def update(self, fields):
        self.client.docs[f"{self.collection}/{self.id}"].update(fields)


class Collection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def document(self, doc_id):
        return Document(self.client, self.name, doc_id)

    def stream(self):
        return (
            Snapshot(path.split("/", 1)[1], data)
            for path, data in self.client.docs.items() if path.startswith(f"{self.name}/")
        )


class FakeFirestoreClient:
    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return Collection(self, name)


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_WEBHOOK_API_KEY", "fixture-key")
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "fixture-project")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fixture-key")
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


def test_webhook_requires_openrouter_key_at_startup(webhook, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        webhook.build_webhook_app()


def test_webhook_wires_firestore_and_jev_without_rescoring(webhook, monkeypatch, profile, job):
    projects = []
    client = FakeFirestoreClient()
    client.docs["profiles/current"] = profile.model_dump()
    client.docs["job_postings/original"] = {"job": job.model_dump(), "status": "PENDING"}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: projects.append(kwargs["project"]) or client)
    scored = []

    class FakeScorer:
        def score(self, candidate, posting):
            scored.append((candidate, posting))
            return ScoreResult(score=80, confidence=0.8, model="typesafe/jev-1.13")

    monkeypatch.setattr(webhook, "JevScoringTool", lambda api_key: FakeScorer())

    app = webhook.build_webhook_app()
    with TestClient(app) as http:
        first = http.post("/webhooks/inference", headers={"X-API-Key": "fixture-key"})
        second = http.post("/webhooks/inference", headers={"X-API-Key": "fixture-key"})

    assert projects == ["fixture-project"]
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == b""
    assert scored == [(profile, job)]
    assert client.docs["job_postings/original"]["status"] == "EVALUATED"
    assert client.docs["job_postings/original"]["score"] == 80
    assert client.docs["job_postings/original"]["job"] == job.model_dump()


def test_webhook_exposes_firestore_credential_failure_at_startup(webhook, monkeypatch):
    def missing_credentials(**kwargs):
        raise ValueError("credentials missing")

    monkeypatch.setattr(webhook.firestore, "Client", missing_credentials)

    with pytest.raises(ValueError, match="credentials missing"):
        webhook.build_webhook_app()
