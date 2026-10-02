"""Read offers from the documents created by local_collector."""

from __future__ import annotations

from google.cloud import firestore

from job_contracts import JobPosting


class FirestorePostingsRepository:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client

    def list_postings(self) -> list[JobPosting]:
        return [
            JobPosting.model_validate(snapshot.to_dict()["job"])
            for snapshot in self.client.collection("job_postings").stream()
        ]
