"""Cloud Firestore storage for the local LinkedIn collector."""

from __future__ import annotations

from datetime import datetime, timezone

from google.api_core.exceptions import Conflict
from google.cloud import firestore

from job_contracts.models import CollectorPlan, JobLead, JobPosting
from job_contracts.keys import job_key, lead_key


class FirestoreCollectorStore:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client

    def load_plan(self) -> CollectorPlan:
        snapshot = self.client.collection("settings").document("search_plan").get()
        if not snapshot.exists:
            raise ValueError("No search plan in Firestore at settings/search_plan")
        return CollectorPlan.model_validate(snapshot.to_dict())

    def known_keys(self, leads: list[JobLead]) -> dict[str, str]:
        if not leads:
            return {}
        collection = self.client.collection("job_postings")
        references = [collection.document(lead_key(lead)) for lead in leads]
        return {snapshot.reference.id: (snapshot.to_dict() or {}).get("status", "PENDING")
                for snapshot in self.client.get_all(references) if snapshot.exists}

    def save(self, job: JobPosting) -> bool:
        document = self.client.collection("job_postings").document(job_key(job))
        try:
            document.create({
                "source": job.source,
                "external_id": job.external_id,
                "job": job.model_dump(),
                "status": "PENDING",
                "ingested_at": datetime.now(timezone.utc),
            })
        except Conflict:
            return False
        return True
