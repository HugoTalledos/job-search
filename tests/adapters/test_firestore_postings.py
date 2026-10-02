import pytest
from pydantic import ValidationError

from job_agent.adapters.persistence.firestore_postings import FirestorePostingsRepository


class Snapshot:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class Collection:
    def __init__(self, documents, error=None):
        self.documents = documents
        self.error = error

    def stream(self):
        yield from (Snapshot(data) for data in self.documents)
        if self.error:
            raise self.error


class Client:
    def __init__(self, documents, error=None):
        self.names = []
        self.documents = documents
        self.error = error

    def collection(self, name):
        self.names.append(name)
        return Collection(self.documents, self.error)


def test_firestore_reads_collector_postings(job):
    client = Client([{"source": "linkedin", "job": job.model_dump(), "ingested_at": "today"}])

    postings = FirestorePostingsRepository(client).list_postings()

    assert client.names == ["job_postings"]
    assert postings == [job]
    assert postings[0].description == job.description


def test_firestore_rejects_malformed_posting():
    client = Client([{"job": {"title": "Incomplete"}}])

    with pytest.raises(ValidationError):
        FirestorePostingsRepository(client).list_postings()


def test_firestore_propagates_error_during_stream(job):
    client = Client([{"job": job.model_dump()}], RuntimeError("Firestore unavailable"))

    with pytest.raises(RuntimeError, match="Firestore unavailable"):
        FirestorePostingsRepository(client).list_postings()
