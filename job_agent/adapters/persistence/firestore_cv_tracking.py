"""Transactional version claims and independent Telegram delivery receipts."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import uuid4

from google.api_core.exceptions import FailedPrecondition
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from ...application.cv_models import ClaimResult, CvArtifacts, CvVersionKey, ReadyCvVersion, LegacyCvLookupUnavailable
from ...domain.models import JobMatch, JobPosting, TailoredResume


class FirestoreCvTrackingStore:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client

    def _parent(self, key: CvVersionKey):
        return self.client.collection('application_tracking').document(key.posting_id)

    def _version(self, key: CvVersionKey):
        return self._parent(key).collection('versions').document(key.version_id)

    def load(self, posting_id: str) -> JobPosting:
        snapshot = self.client.collection('job_postings').document(posting_id).get()
        if not snapshot.exists:
            raise LookupError('Job posting does not exist')
        return JobPosting.model_validate(snapshot.to_dict()['job'])

    def claim(self, key: CvVersionKey, now: datetime) -> ClaimResult:
        """Only one worker acquires a live lease; FAILED/expired versions are retryable."""
        parent, version = self._parent(key), self._version(key)
        attempt_id = uuid4().hex

        @firestore.transactional
        def claim_version(transaction):
            data = version.get(transaction=transaction).to_dict() or {}
            if data.get('generation_status') == 'READY':
                return ClaimResult('reuse')
            lease = data.get('lease_expires_at')
            if data.get('generation_status') == 'PROCESSING' and lease and lease > now:
                return ClaimResult('in_progress')
            parent_data = parent.get(transaction=transaction).to_dict() or {}
            transaction.set(parent, {
                'posting_ref': f'job_postings/{key.posting_id}',
                'first_requested_at': parent_data.get('first_requested_at', now),
                'latest_version_id': key.version_id,
                'updated_at': now,
            }, merge=True)
            transaction.set(version, {
                **asdict(key),
                'generation_status': 'PROCESSING',
                'delivery_status': 'PENDING',
                'requested_at': data.get('requested_at', now),
                'claimed_at': now,
                'attempt_id': attempt_id,
                'lease_expires_at': now + timedelta(minutes=30),
                'updated_at': now,
            }, merge=True)
            return ClaimResult('generate', attempt_id)

        return claim_version(self.client.transaction())

    def mark_ready(
        self, key: CvVersionKey, artifacts: CvArtifacts, match: JobMatch, tailored: TailoredResume,
        *, attempt_id: str,
    ) -> None:
        """Called only after all uploads succeed; publish version and parent stage atomically."""
        version = self._version(key)

        @firestore.transactional
        def ready(transaction):
            data = version.get(transaction=transaction).to_dict() or {}
            if (data.get('generation_status') != 'PROCESSING'
                    or not attempt_id or data.get('attempt_id') != attempt_id):
                raise ValueError('CV generation claim is no longer active')
            transaction.set(version, {
                'artifacts': asdict(artifacts),
                'match': match.model_dump(mode='json'),
                'tailored': tailored.model_dump(mode='json'),
                'generation_status': 'READY',
                'delivery_status': 'PENDING',
                'lease_expires_at': None,
                'ready_at': firestore.SERVER_TIMESTAMP,
                'updated_at': firestore.SERVER_TIMESTAMP,
            }, merge=True)
            transaction.set(self._parent(key), {
                'stage': 'CV_READY',
                'updated_at': firestore.SERVER_TIMESTAMP,
            }, merge=True)

        ready(self.client.transaction())

    def mark_failed(self, key: CvVersionKey, *, attempt_id: str) -> None:
        version = self._version(key)

        @firestore.transactional
        def fail(transaction):
            data = version.get(transaction=transaction).to_dict() or {}
            # A late failure (including delivery) must never invalidate ready artifacts.
            if (data.get('generation_status') == 'PROCESSING'
                    and attempt_id and data.get('attempt_id') == attempt_id):
                transaction.set(version, {
                    'generation_status': 'FAILED',
                    'lease_expires_at': None,
                    'failed_at': firestore.SERVER_TIMESTAMP,
                    'updated_at': firestore.SERVER_TIMESTAMP,
                }, merge=True)

        fail(self.client.transaction())

    def load_ready(self, key: CvVersionKey) -> ReadyCvVersion:
        data = self._version(key).get().to_dict() or {}
        if data.get('generation_status') != 'READY':
            raise LookupError('CV version is not ready')
        return ReadyCvVersion(
            key=key,
            artifacts=CvArtifacts(**data['artifacts']),
            match=JobMatch.model_validate(data['match']),
            tailored=TailoredResume.model_validate(data['tailored']),
            summary_message_id=data.get('summary_message_id'),
            pdf_message_id=data.get('pdf_message_id'),
            delivery_status=data['delivery_status'],
        )

    def find_ready_by_pdf_message(self, message_id: int) -> ReadyCvVersion | None:
        if type(message_id) is not int or message_id <= 0:
            return None
        indexed = self.client.collection('cv_pdf_messages').document(str(message_id)).get().to_dict()
        if indexed:
            return self.load_ready(CvVersionKey(**indexed['key']))
        # Legacy buttonless PDFs predate the receipt index.
        try:
            matches = list(self.client.collection_group('versions').where(
                filter=FieldFilter('pdf_message_id', '==', message_id)).limit(2).stream())
        except FailedPrecondition:
            raise LegacyCvLookupUnavailable('Historical PDF lookup index is unavailable') from None
        if len(matches) != 1:
            return None
        data = matches[0].to_dict()
        if data.get('generation_status') != 'READY':
            return None
        key = CvVersionKey(**{field: data[field] for field in CvVersionKey.__dataclass_fields__ if field in data})
        return self.load_ready(key)

    def begin_delivery(self, key: CvVersionKey) -> None:
        """A fresh explicit resend must not mistake old receipts for new delivery."""
        version = self._version(key)

        @firestore.transactional
        def begin(transaction):
            data = version.get(transaction=transaction).to_dict() or {}
            if data.get('generation_status') != 'READY':
                raise LookupError('CV version is not ready for delivery')
            if data.get('delivery_status') == 'SENT':
                transaction.set(version, {
                    'summary_message_id': None, 'pdf_message_id': None,
                    'summary_sent_at': None, 'pdf_sent_at': None,
                    'delivery_status': 'PENDING', 'updated_at': firestore.SERVER_TIMESTAMP,
                }, merge=True)

        begin(self.client.transaction())

    def mark_summary_sent(self, key: CvVersionKey, message_id: int) -> None:
        self._mark_delivery(key, 'summary_message_id', message_id)

    def mark_pdf_sent(self, key: CvVersionKey, message_id: int) -> None:
        self._mark_delivery(key, 'pdf_message_id', message_id)

    def mark_delivery_failed(self, key: CvVersionKey) -> None:
        self._mark_delivery(key)

    def _mark_delivery(self, key: CvVersionKey, receipt: str | None = None, message_id: int | None = None) -> None:
        version = self._version(key)

        @firestore.transactional
        def deliver(transaction):
            data = version.get(transaction=transaction).to_dict() or {}
            if data.get('generation_status') != 'READY':
                raise LookupError('CV version is not ready for delivery')
            changes = {'updated_at': firestore.SERVER_TIMESTAMP}
            if receipt:
                changes[receipt] = message_id
                changes[receipt.removesuffix('_message_id') + '_sent_at'] = firestore.SERVER_TIMESTAMP
                data[receipt] = message_id
                changes['delivery_status'] = (
                    'SENT' if data.get('summary_message_id') is not None and data.get('pdf_message_id') is not None
                    else 'PENDING'
                )
            else:
                changes['delivery_status'] = 'FAILED'
                changes['delivery_failed_at'] = firestore.SERVER_TIMESTAMP
            transaction.set(version, changes, merge=True)
            if receipt == 'pdf_message_id':
                transaction.set(self.client.collection('cv_pdf_messages').document(str(message_id)), {'key': asdict(key)})

        deliver(self.client.transaction())
