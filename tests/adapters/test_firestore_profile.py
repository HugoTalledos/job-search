from datetime import datetime, timezone

from job_agent.adapters.persistence import FirestoreProfileStore
from job_agent.domain.models import RepoEvidence, StoredProfile
from job_agent.scoring.firestore import FirestoreScoringStore

BUILT = datetime(2026, 10, 3, tzinfo=timezone.utc)


class Snapshot:
    def __init__(self, data):
        self.exists = data is not None
        self.data = data

    def to_dict(self):
        return self.data


class Document:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def get(self):
        return Snapshot(self.client.docs.get(self.path))

    def set(self, data):
        self.client.docs[self.path] = data


class Collection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def document(self, doc_id):
        return Document(self.client, f"{self.name}/{doc_id}")


class Client:
    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return Collection(self, name)


def test_saved_profile_round_trips_and_is_readable_by_scoring(profile):
    client = Client()
    stored = StoredProfile(
        profile=profile, resume_fingerprint="r", repos_fingerprint="g", built_at=BUILT, repos_checked_at=BUILT,
        repositories=[RepoEvidence(url="https://g/a", head="h1", summary="Python")],
    )

    FirestoreProfileStore(client).save(stored)

    assert client.docs["profiles/current"]["full_name"] == profile.full_name
    assert FirestoreProfileStore(client).load() == stored
    assert FirestoreScoringStore(client).load() == profile


def test_missing_profile_loads_as_none():
    assert FirestoreProfileStore(Client()).load() is None


def test_profile_written_by_hand_without_metadata_loads(profile):
    client = Client()
    client.docs["profiles/current"] = profile.model_dump()

    stored = FirestoreProfileStore(client).load()

    assert stored.profile == profile and stored.resume_fingerprint == ""


def test_invalid_profile_loads_as_none():
    client = Client()
    client.docs["profiles/current"] = {"full_name": "Incomplete"}

    assert FirestoreProfileStore(client).load() is None
