from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock

import pytest
from google.api_core.exceptions import Aborted

# Keep the SDK's transactional decorator and retries real; emulate only document I/O.
class Snapshot:
    def __init__(self, data):
        self.exists = data is not None
        self.data = deepcopy(data)

    def to_dict(self):
        return deepcopy(self.data)


class Document:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def collection(self, name):
        return Collection(self.client, f'{self.path}/{name}')

    def get(self, transaction=None):
        with self.client.lock:
            snapshot = Snapshot(self.client.docs.get(self.path))
            if transaction:
                assert not transaction.writes, 'Firestore forbids reads after writes'
                transaction.reads[self.path] = self.client.revisions.get(self.path, 0)
        if transaction and self.client.barrier and self.client.barrier_on(self.path) and transaction.attempt == 1:
            self.client.barrier.wait(timeout=5)
        return snapshot

    def set(self, data, merge=False):
        with self.client.lock:
            self.client.docs[self.path] = {**(self.client.docs.get(self.path, {}) if merge else {}), **deepcopy(data)}
            self.client.revisions[self.path] = self.client.revisions.get(self.path, 0) + 1


class Collection:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def document(self, doc_id):
        return Document(self.client, f'{self.path}/{doc_id}')


class Transaction:
    _read_only = False
    _max_attempts = 5

    def __init__(self, client):
        self.client, self.attempt = client, 0

    def _clean_up(self):
        self.reads, self.writes, self._id = {}, [], None

    def _begin(self, retry_id=None):
        self.attempt += 1
        self._id = b'test-transaction'

    def set(self, document, data, merge=False):
        self.writes.append((document.path, data, merge))

    def _commit(self):
        with self.client.lock:
            if any(self.client.revisions.get(path, 0) != rev for path, rev in self.reads.items()):
                self.client.conflicts += 1
                raise Aborted('Concurrent document change')
            for path, data, merge in self.writes:
                self.client.docs[path] = {**(self.client.docs.get(path, {}) if merge else {}), **data}
                self.client.revisions[path] = self.client.revisions.get(path, 0) + 1

    def _rollback(self):
        self._clean_up()


class Client:
    def __init__(self):
        self.docs, self.revisions = {}, {}
        self.lock, self.barrier, self.conflicts = Lock(), None, 0
        self.barrier_on = lambda path: '/versions/' in path  # which transactional reads wait at the barrier

    def collection(self, name):
        return Collection(self, name)

    def transaction(self):
        return Transaction(self)

    def collection_group(self, name):
        client = self
        class Query:
            def where(self, *, filter):
                self.filter = filter
                return self
            def limit(self, count):
                self.count = count
                return self
            def stream(self):
                return iter([Snapshot(data) for path, data in client.docs.items()
                    if path.split('/')[-2] == name and data.get(self.filter.field_path) == self.filter.value][:self.count])
        return Query()


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def store(client):
    from job_agent.adapters.persistence.firestore_cv_tracking import FirestoreCvTrackingStore
    return FirestoreCvTrackingStore(client)


@pytest.fixture
def key():
    from job_agent.application.cv_models import CvVersionKey
    return CvVersionKey('offer', 'resume', 'profile', 'job')


@pytest.fixture
def now():
    return datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


@pytest.fixture
def artifacts():
    from job_agent.application.cv_models import CvArtifacts
    return CvArtifacts('gs://bucket/cv.pdf', 'gs://bucket/resume.md', 'gs://bucket/README.md')


def version(client, key):
    return client.docs[f'application_tracking/{key.posting_id}/versions/{key.version_id}']


def test_key_identity_is_stable_unambiguous_and_sensitive_to_each_input(key):
    from job_agent.application.cv_models import CvVersionKey
    assert key.version_id == CvVersionKey('offer', 'resume', 'profile', 'job').version_id
    assert len(key.version_id) == 64
    assert all(c in '0123456789abcdef' for c in key.version_id)
    for field in ('posting_id', 'resume_fingerprint', 'profile_fingerprint', 'job_fingerprint'):
        assert replace(key, **{field: 'changed'}).version_id != key.version_id
    assert CvVersionKey('a', 'bc', 'd', 'e').version_id != CvVersionKey('ab', 'c', 'd', 'e').version_id


def test_claim_is_atomic_across_workers(store, client, key, now):
    client.barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = list(pool.map(lambda _: store.claim(key, now).action, range(2)))
    assert sorted(actions) == ['generate', 'in_progress']
    assert client.conflicts == 1  # Both read missing; SDK retries the losing transaction.


def test_claim_records_inputs_and_lease_without_marking_cv_ready(store, client, key, now):
    assert store.claim(key, now).action == 'generate'
    data = version(client, key)
    assert data['generation_status'] == 'PROCESSING'
    assert data['delivery_status'] == 'PENDING'
    assert data['lease_expires_at'] == now + timedelta(minutes=30)
    assert data['resume_fingerprint'] == 'resume'
    assert data['profile_fingerprint'] == 'profile'
    assert data['job_fingerprint'] == 'job'
    parent = client.docs['application_tracking/offer']
    assert parent['first_requested_at'] == now
    assert parent['latest_version_id'] == key.version_id
    assert parent['posting_ref'] == 'job_postings/offer'
    assert parent.get('stage') != 'CV_READY'


def test_live_lease_blocks_but_expired_lease_reclaims(store, key, now):
    claim = store.claim(key, now)
    assert store.claim(key, now + timedelta(minutes=29, seconds=59)).action == 'in_progress'
    assert store.claim(key, now + timedelta(minutes=30)).action == 'generate'
    assert store.claim(key, now + timedelta(minutes=31)).action == 'in_progress'


def test_failed_version_reclaims_and_preserves_first_request(store, client, key, now):
    claim = store.claim(key, now)
    store.mark_failed(key, attempt_id=claim.attempt_id)
    assert version(client, key)['generation_status'] == 'FAILED'
    assert store.claim(key, now + timedelta(minutes=1)).action == 'generate'
    newer = replace(key, resume_fingerprint='new')
    store.claim(newer, now + timedelta(days=1))
    assert client.docs['application_tracking/offer']['first_requested_at'] == now
    assert client.docs['application_tracking/offer']['latest_version_id'] == newer.version_id
    assert version(client, key)['requested_at'] == now


def test_complete_artifacts_and_analysis_make_version_reusable(store, client, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    assert client.docs['application_tracking/offer']['stage'] == 'CV_READY'
    assert store.claim(key, now + timedelta(days=1)).action == 'reuse'
    ready = store.load_ready(key)
    assert (ready.key, ready.artifacts, ready.match, ready.tailored) == (key, artifacts, match, tailored)
    assert ready.delivery_status == 'PENDING'
    assert ready.summary_message_id is None and ready.pdf_message_id is None
    assert version(client, key)['generation_status'] == 'READY'
    assert version(client, key)['lease_expires_at'] is None


def test_delivery_failure_keeps_ready_artifacts_and_receipt_for_retry(store, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    store.mark_summary_sent(key, 41)
    assert store.load_ready(key).delivery_status == 'PENDING'
    store.mark_delivery_failed(key)
    ready = store.load_ready(key)
    assert ready.delivery_status == 'FAILED'
    assert ready.summary_message_id == 41
    assert ready.artifacts == artifacts
    assert store.claim(key, now).action == 'reuse'
    store.mark_pdf_sent(key, 42)
    ready = store.load_ready(key)
    assert ready.delivery_status == 'SENT'
    assert ready.summary_message_id == 41 and ready.pdf_message_id == 42


def test_pdf_receipt_alone_is_not_complete_delivery(store, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    store.mark_pdf_sent(key, 1)
    assert store.load_ready(key).delivery_status == 'PENDING'
    store.mark_summary_sent(key, 2)
    assert store.load_ready(key).delivery_status == 'SENT'


def test_failure_cannot_downgrade_ready_generation(store, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    store.mark_failed(key, attempt_id=claim.attempt_id)
    assert store.load_ready(key).artifacts == artifacts


def test_ready_cannot_be_overwritten(store, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    with pytest.raises(ValueError):
        store.mark_ready(key, replace(artifacts, pdf_uri='gs://other/file'), match, tailored, attempt_id=claim.attempt_id)
    assert store.load_ready(key).artifacts == artifacts


def test_load_ready_rejects_missing_and_incomplete_versions(store, key, now):
    with pytest.raises(LookupError):
        store.load_ready(key)
    claim = store.claim(key, now)
    with pytest.raises(LookupError):
        store.load_ready(key)


def test_load_posting_uses_exact_document_id(store, client, job):
    client.docs['job_postings/offer'] = {'job': job.model_dump(), 'status': 'NOTIFIED'}
    assert store.load('offer') == job
    assert client.docs['job_postings/offer']['status'] == 'NOTIFIED'
    with pytest.raises(LookupError):
        store.load('123')


def test_concurrent_receipts_are_both_retained(store, client, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    client.barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store.mark_summary_sent, key, 51), pool.submit(store.mark_pdf_sent, key, 52)]
        for future in futures:
            future.result()
    ready = store.load_ready(key)
    assert ready.delivery_status == 'SENT'
    assert ready.summary_message_id == 51 and ready.pdf_message_id == 52
    assert client.conflicts == 1


def test_partial_storage_upload_never_publishes_ready_tracking(store, client, key, now):
    from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
    from tests.adapters.test_firebase_cv_artifacts import Client as StorageClient
    storage_client = StorageClient()
    storage_client.fail_on = 'README.md'
    claim = store.claim(key, now)
    with pytest.raises(RuntimeError, match='upload failed'):
        FirebaseCvArtifactStore('private-bucket', storage_client).save(
            key, b'pdf', 'cv', 'readme', attempt_id=claim.attempt_id,
        )
    assert version(client, key)['generation_status'] == 'PROCESSING'
    assert client.docs['application_tracking/offer'].get('stage') != 'CV_READY'
    with pytest.raises(LookupError):
        store.load_ready(key)


def test_reclaimed_attempt_fences_stale_success_and_failure(store, client, key, now, artifacts, match, tailored):
    first = store.claim(key, now)
    second = store.claim(key, now + timedelta(minutes=30))
    assert first.attempt_id and second.attempt_id and first.attempt_id != second.attempt_id
    with pytest.raises(ValueError, match='claim'):
        store.mark_ready(key, artifacts, match, tailored, attempt_id=first.attempt_id)
    store.mark_failed(key, attempt_id=first.attempt_id)
    assert version(client, key)['generation_status'] == 'PROCESSING'
    assert version(client, key)['attempt_id'] == second.attempt_id
    store.mark_ready(key, artifacts, match, tailored, attempt_id=second.attempt_id)
    assert store.load_ready(key).artifacts == artifacts


def test_non_generation_claims_do_not_authorize_finalization(store, key, now, artifacts, match, tailored):
    first = store.claim(key, now)
    assert store.claim(key, now).attempt_id is None
    store.mark_ready(key, artifacts, match, tailored, attempt_id=first.attempt_id)
    assert store.claim(key, now).attempt_id is None


def test_stale_upload_after_new_attempt_is_ready_cannot_change_published_bytes(store, key, now, match, tailored):
    from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
    from tests.adapters.test_firebase_cv_artifacts import Client as StorageClient
    storage_client = StorageClient()
    artifact_store = FirebaseCvArtifactStore('private-bucket', storage_client)

    # A pauses before upload; B reclaims after expiry and completes all publication.
    first = store.claim(key, now)
    winner = store.claim(key, now + timedelta(minutes=30))
    winning_artifacts = artifact_store.save(
        key, b'winner pdf', 'winner cv', 'winner readme', attempt_id=winner.attempt_id,
    )
    store.mark_ready(key, winning_artifacts, match, tailored, attempt_id=winner.attempt_id)
    winning_objects = dict(storage_client.objects)

    # A resumes after B is READY. Its writes must never address B's immutable objects.
    stale_artifacts = artifact_store.save(
        key, b'stale pdf', 'stale cv', 'stale readme', attempt_id=first.attempt_id,
    )
    with pytest.raises(ValueError, match='claim'):
        store.mark_ready(key, stale_artifacts, match, tailored, attempt_id=first.attempt_id)
    store.mark_failed(key, attempt_id=first.attempt_id)

    ready = store.load_ready(key)
    assert ready.artifacts == winning_artifacts
    assert stale_artifacts.pdf_uri != ready.artifacts.pdf_uri
    assert artifact_store.read_pdf(ready.artifacts) == b'winner pdf'
    assert all(storage_client.objects[name] == value for name, value in winning_objects.items())
    assert store.claim(key, now + timedelta(hours=1)).action == 'reuse'


def test_fresh_resend_resets_receipts_and_failed_pdf_retry_keeps_new_summary(store, key, now, artifacts, match, tailored):
    claim = store.claim(key, now)
    store.mark_ready(key, artifacts, match, tailored, attempt_id=claim.attempt_id)
    store.mark_summary_sent(key, 41)
    store.mark_pdf_sent(key, 42)
    store.begin_delivery(key)
    ready = store.load_ready(key)
    assert ready.delivery_status == 'PENDING'
    assert ready.summary_message_id is None and ready.pdf_message_id is None
    store.mark_summary_sent(key, 51)
    store.mark_delivery_failed(key)
    store.begin_delivery(key)  # A partial retry must preserve the new confirmed summary.
    ready = store.load_ready(key)
    assert ready.summary_message_id == 51 and ready.pdf_message_id is None
    assert ready.delivery_status == 'FAILED'
    store.mark_pdf_sent(key, 52)
    ready = store.load_ready(key)
    assert ready.delivery_status == 'SENT'
    assert ready.summary_message_id == 51 and ready.pdf_message_id == 52


def test_begin_delivery_rejects_generation_that_is_not_ready(store, key, now):
    store.claim(key, now)
    with pytest.raises(LookupError):
        store.begin_delivery(key)


def test_find_ready_pdf_uses_receipt_and_old_buttonless_version(store, key, now, artifacts, match, tailored):
    claim=store.claim(key,now)
    store.mark_ready(key,artifacts,match,tailored,attempt_id=claim.attempt_id)
    store.mark_pdf_sent(key,73)
    assert store.find_ready_by_pdf_message(73).key == key
    # Old versions have no new receipt index: query historical version documents.
    store.client.docs.pop('cv_pdf_messages/73',None)
    assert store.find_ready_by_pdf_message(73).artifacts == artifacts
    assert store.find_ready_by_pdf_message(999) is None


def test_delivery_lease_excludes_competitor_expires_and_fences_old_worker(store,key,now,artifacts,match,tailored):
    claim=store.claim(key,now)
    store.mark_ready(key,artifacts,match,tailored,attempt_id=claim.attempt_id)
    first=store.claim_delivery(key,now)
    assert first.action=='deliver'
    assert store.claim_delivery(key,now).action=='in_progress'
    store.mark_summary_sent(key,71,delivery_attempt_id=first.attempt_id)
    second=store.claim_delivery(key,now+timedelta(minutes=6))
    assert second.action=='deliver' and second.attempt_id!=first.attempt_id
    assert store.load_ready(key).summary_message_id==71
    with pytest.raises(ValueError): store.renew_delivery(key,first.attempt_id,now+timedelta(minutes=6))
    with pytest.raises(ValueError): store.mark_pdf_sent(key,72,delivery_attempt_id=first.attempt_id)
    store.mark_delivery_failed(key,delivery_attempt_id=first.attempt_id)
    assert store.claim_delivery(key,now+timedelta(minutes=6)).action=='in_progress'
    store.mark_pdf_sent(key,73,delivery_attempt_id=second.attempt_id)
    assert store.claim_delivery(key,now+timedelta(minutes=6)).action=='sent'


def test_delivery_lease_is_transactional_for_concurrent_claims(store,client,key,now,artifacts,match,tailored):
    claim=store.claim(key,now)
    store.mark_ready(key,artifacts,match,tailored,attempt_id=claim.attempt_id)
    client.barrier=Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:store.claim_delivery(key,now),range(2)))
    assert sorted(c.action for c in claims)==['deliver','in_progress']


def test_explicit_resend_reserves_and_resets_receipts_atomically(store,key,now,artifacts,match,tailored):
    claim=store.claim(key,now)
    store.mark_ready(key,artifacts,match,tailored,attempt_id=claim.attempt_id)
    store.mark_summary_sent(key,71)
    store.mark_pdf_sent(key,72)
    delivery=store.claim_delivery(key,now,resend=True)
    assert delivery.action=='deliver'
    ready=store.load_ready(key)
    assert ready.summary_message_id is None and ready.pdf_message_id is None
    assert store.claim_delivery(key,now,resend=True).action=='in_progress'
