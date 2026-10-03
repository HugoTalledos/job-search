import logging

import pytest
from google.cloud import firestore
from pydantic import ValidationError

from job_agent.scoring.firestore import FirestoreScoringStore
from job_agent.scoring import models
from job_agent.scoring.models import ScoreResult


class Snapshot:
    def __init__(self, reference, data):
        self.reference = reference
        self.id = reference.id
        self.exists = data is not None
        self.data = data

    def to_dict(self):
        return self.data


class Document:
    def __init__(self, client, collection, doc_id):
        self.client = client
        self.path = f"{collection}/{doc_id}"
        self.id = doc_id

    def get(self):
        return Snapshot(self, self.client.docs.get(self.path))

    def update(self, fields):
        self.client.updates.append((self.path, fields))
        self.client.docs[self.path].update(fields)


class Collection:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def document(self, doc_id):
        return Document(self.client, self.name, doc_id)

    def stream(self):
        for path, data in self.client.docs.items():
            if path.startswith(f"{self.name}/"):
                yield Snapshot(self.document(path.split("/", 1)[1]), data)
        if self.client.stream_error:
            raise self.client.stream_error


class Client:
    def __init__(self):
        self.docs = {}
        self.updates = []
        self.stream_error = None

    def collection(self, name):
        return Collection(self, name)


def test_loads_profile_from_current_document(profile):
    client = Client()
    client.docs["profiles/current"] = profile.model_dump()

    assert FirestoreScoringStore(client).load() == profile


def test_missing_profile_is_an_error():
    with pytest.raises(ValueError, match="profiles/current"):
        FirestoreScoringStore(Client()).load()


def test_invalid_profile_is_an_error():
    client = Client()
    client.docs["profiles/current"] = {"full_name": "Incomplete"}

    with pytest.raises(ValidationError):
        FirestoreScoringStore(client).load()


def test_lists_explicit_and_legacy_pending_with_original_ids(job, caplog):
    client = Client()
    client.docs = {
        "job_postings/explicit": {"status": "PENDING", "job": job.model_dump()},
        "job_postings/legacy": {"job": job.model_dump()},
        "job_postings/done": {"status": "EVALUATED", "job": job.model_dump()},
        "job_postings/waiting": {"status": "PENDING_NOTIFICATION", "job": job.model_dump()},
        "job_postings/notified": {"status": "NOTIFIED", "job": job.model_dump()},
        "job_postings/unknown": {"status": "OTHER", "job": job.model_dump()},
        "job_postings/broken": {"status": "PENDING", "job": {"title": "Incomplete"}},
    }

    with caplog.at_level(logging.WARNING):
        pending = FirestoreScoringStore(client).list_pending()

    assert [posting.document_id for posting in pending] == ["explicit", "legacy"]
    assert [posting.job for posting in pending] == [job, job]
    assert "unknown" in caplog.text and "broken" in caplog.text
    assert job.description not in caplog.text


def test_stream_failure_aborts_listing(job):
    client = Client()
    client.docs["job_postings/first"] = {"job": job.model_dump()}
    client.stream_error = RuntimeError("stream stopped")

    with pytest.raises(RuntimeError, match="stream stopped"):
        FirestoreScoringStore(client).list_pending()


def test_updates_score_on_original_document_without_overwriting_job(job):
    client = Client()
    original = {"status": "PENDING", "job": job.model_dump(), "source": "linkedin"}
    client.docs["job_postings/original-id"] = original.copy()

    FirestoreScoringStore(client).mark_evaluated(
        "original-id", ScoreResult(score=83, confidence=0.7, model="typesafe/jev-1.13")
    )

    [(path, fields)] = client.updates
    assert path == "job_postings/original-id"
    assert fields["status"] == "EVALUATED"
    assert fields["score"] == 83
    assert fields["confidence"] == 0.7
    assert fields["score_model"] == "typesafe/jev-1.13"
    assert fields["evaluated_at"] is firestore.SERVER_TIMESTAMP
    assert client.docs[path]["job"] == job.model_dump()
    assert client.docs[path]["source"] == "linkedin"


def test_pending_posting_reuses_saved_enrichment_even_when_fields_are_empty(job):
    client = Client()
    client.docs["job_postings/enriched"] = {
        "status": "PENDING", "job": job.model_dump(), "enriched_at": "previous",
        "required_language": None, "salary_range": None,
    }
    client.docs["job_postings/fresh"] = {"status": "PENDING", "job": job.model_dump()}

    pending = FirestoreScoringStore(client).list_pending()

    assert pending[0].enrichment == models.PostingEnrichment()
    assert pending[1].enrichment is None


def test_invalid_saved_enrichment_skips_document_and_logs_only_id(job, caplog):
    client = Client()
    client.docs["job_postings/bad"] = {
        "status": "PENDING", "job": job.model_dump(), "enriched_at": "previous",
        "required_language": ["English"],
    }
    client.docs["job_postings/good"] = {"status": "PENDING", "job": job.model_dump()}

    with caplog.at_level(logging.WARNING):
        pending = FirestoreScoringStore(client).list_pending()

    assert [item.document_id for item in pending] == ["good"]
    assert "bad" in caplog.text
    assert job.description not in caplog.text


def test_enrichment_and_score_updates_preserve_original_job(job):
    client = Client()
    path = "job_postings/original"
    client.docs[path] = {"status": "PENDING", "job": job.model_dump(), "source": "linkedin"}
    store = FirestoreScoringStore(client)

    store.mark_enriched("original", models.PostingEnrichment("English B2", "$2,000 - $3,000"))
    store.mark_scored("original", ScoreResult(83, 0.7, "typesafe/jev-1.13"), notify=True)
    store.mark_notified("original")

    assert [update[0] for update in client.updates] == [path, path, path]
    enriched, scored, notified = [fields for _, fields in client.updates]
    assert enriched == {
        "required_language": "English B2", "salary_range": "$2,000 - $3,000",
        "enriched_at": firestore.SERVER_TIMESTAMP,
    }
    assert scored == {
        "status": "PENDING_NOTIFICATION", "score": 83, "confidence": 0.7,
        "score_model": "typesafe/jev-1.13", "evaluated_at": firestore.SERVER_TIMESTAMP,
    }
    assert notified == {"status": "NOTIFIED", "notified_at": firestore.SERVER_TIMESTAMP}
    assert client.docs[path]["job"] == job.model_dump()
    assert client.docs[path]["source"] == "linkedin"


def test_below_threshold_score_becomes_evaluated(job):
    client = Client()
    client.docs["job_postings/low"] = {"status": "PENDING", "job": job.model_dump()}

    FirestoreScoringStore(client).mark_scored("low", ScoreResult(69, 0.6, "typesafe/jev-1.13"), notify=False)

    assert client.docs["job_postings/low"]["status"] == "EVALUATED"
    assert "notified_at" not in client.docs["job_postings/low"]
