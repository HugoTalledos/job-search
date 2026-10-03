import pytest

from local_collector.adapters.firestore_store import FirestoreCollectorStore
from job_agent.domain.models import JobLead
from job_agent.domain.policies import job_key, lead_key


class FakeSnapshot:
    def __init__(self, reference, data=None):
        self.reference = reference
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return self._data


class FakeDocument:
    def __init__(self, client, collection, doc_id):
        self.client = client
        self.path = f"{collection}/{doc_id}"
        self.id = doc_id

    def get(self):
        return FakeSnapshot(self, self.client.docs.get(self.path))

    def create(self, data):
        if self.client.fail_create:
            raise self.client.fail_create
        if self.path in self.client.docs:
            from google.api_core.exceptions import Conflict

            raise Conflict("already exists")
        self.client.docs[self.path] = data

    def set(self, data):
        self.client.docs[self.path] = data


class FakeCollection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def document(self, doc_id):
        return FakeDocument(self.client, self.name, doc_id)


class FakeFirestore:
    def __init__(self):
        self.docs = {}
        self.read_batches = []
        self.fail_create = None

    def collection(self, name):
        return FakeCollection(self, name)

    def get_all(self, references):
        self.read_batches.append([ref.path for ref in references])
        return [ref.get() for ref in references]


def test_firestore_loads_plan_from_settings_document():
    client = FakeFirestore()
    client.docs["settings/search_plan"] = {
        "search": {"queries": [{"keywords": "backend", "location": "Colombia"}], "posted_within_days": 2},
        "max_details_per_run": 12,
    }

    plan = FirestoreCollectorStore(client).load_plan()

    assert plan.search.queries[0].keywords == "backend"
    assert plan.max_details_per_run == 12


def test_firestore_saves_plan_to_settings_document():
    from job_agent.domain.models import CollectorPlan, SearchPlan, SearchQuery

    client = FakeFirestore()
    plan = CollectorPlan(search=SearchPlan(queries=[SearchQuery(keywords="backend")], posted_within_days=2),
                         max_details_per_run=12)

    FirestoreCollectorStore(client).save_plan(plan)

    saved = client.docs["settings/search_plan"]
    assert saved["search"]["queries"][0]["keywords"] == "backend"
    assert saved["max_details_per_run"] == 12


def test_firestore_checks_only_requested_lead_documents_in_one_batch():
    client = FakeFirestore()
    leads = [JobLead(source="linkedin", external_id="101"), JobLead(source="linkedin", external_id="102")]
    client.docs[f"job_postings/{lead_key(leads[0])}"] = {"source": "linkedin"}

    known = FirestoreCollectorStore(client).known_keys(leads)

    assert known == {lead_key(leads[0])}
    assert client.read_batches == [[f"job_postings/{lead_key(lead)}" for lead in leads]]


def test_firestore_creates_complete_posting_once(job):
    client = FakeFirestore()
    store = FirestoreCollectorStore(client)

    assert store.save(job) is True
    assert store.save(job) is False
    saved = client.docs[f"job_postings/{job_key(job)}"]
    assert saved["source"] == job.source and saved["external_id"] == job.external_id
    assert saved["job"]["description"] == job.description
    assert saved["status"] == "PENDING"
    assert "ingested_at" in saved
    assert len(client.docs) == 1


def test_firestore_does_not_truncate_oversized_posting_error(job):
    client = FakeFirestore()
    client.fail_create = ValueError("document exceeds 1 MiB")

    with pytest.raises(ValueError, match="1 MiB"):
        FirestoreCollectorStore(client).save(job)

    assert client.docs == {}
