"""User-confirmed applications, one document per original posting."""

from __future__ import annotations

from google.cloud import firestore

from job_contracts import JobPosting


class FirestoreAppliedProposals:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client

    def mark_applied(self, posting_id: str) -> bool:
        """Copy the posting on first confirmation; return False for subsequent presses."""
        source = self.client.collection('job_postings').document(posting_id)
        target = self.client.collection('applied_proposals').document(posting_id)

        @firestore.transactional
        def mark(transaction):
            if target.get(transaction=transaction).exists:
                return False
            snapshot = source.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError('Job posting does not exist')
            data = snapshot.to_dict() or {}
            job = JobPosting.model_validate(data['job'])
            transaction.set(target, {
                'posting_ref': f'job_postings/{posting_id}',
                'job': job.model_dump(mode='json'),
                'applied_at': firestore.SERVER_TIMESTAMP,
            })
            return True

        return mark(self.client.transaction())
