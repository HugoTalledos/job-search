from dataclasses import replace
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from job_agent.application.cv_models import CvVersionKey
from job_agent.adapters.persistence.firestore_cv_reviews import FirestoreCvReviewStore, StaleCvRevision
from tests.adapters.test_firestore_cv_tracking import Client


def test_resume_ownership_and_approval():
    store = FirestoreCvReviewStore(Client())
    key = CvVersionKey('offer', 'r', 'p', 'j')
    review = store.create_or_resume('offer', '123', key)
    assert store.create_or_resume('offer', '123', key) == review
    with pytest.raises(ValueError):
        store.load(review.review_id, 'other')
    first = store.publish_revision(review.review_id, None, 'gs://private/a.md', None)
    second = store.publish_revision(review.review_id, first.revision_id, 'gs://private/b.md', 'proposal')
    assert store.approve(review.review_id, first.revision_id, '123').status == 'stale'
    assert store.approve(review.review_id, second.revision_id, 'other').status == 'unknown'
    assert store.approve(review.review_id, second.revision_id, '123').status == 'approved'
    assert store.approve(review.review_id, second.revision_id, '123').status == 'already_approved'
    with pytest.raises(StaleCvRevision):
        store.publish_revision(review.review_id, second.revision_id, 'gs://private/c.md', None)
    assert store.create_or_resume('offer', '123', key).review_id != review.review_id


def test_concurrent_revision_publication_has_one_winner():
    client = Client()
    store = FirestoreCvReviewStore(client)
    review = store.create_or_resume('offer', '123', CvVersionKey('offer', 'r', 'p', 'j'))
    client.barrier = Barrier(2)
    client.barrier_on = lambda path: path == 'cv_reviews/' + review.review_id
    def publish(n):
        try:
            return store.publish_revision(review.review_id, None, f'gs://private/{n}.md', None).revision_id
        except StaleCvRevision:
            return 'stale'
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(publish, [1, 2]))
    assert results.count('stale') == 1
    assert client.conflicts == 1


def test_correction_version_preserves_legacy_hash():
    key = CvVersionKey('offer', 'r', 'p', 'j')
    assert key.version_id == sha256(b'["offer","r","p","j"]').hexdigest()
    assert replace(key, corrections_version=1).version_id != key.version_id


@pytest.mark.parametrize('chat_id', ['', '-123', '0', 'group'])
def test_review_requires_private_chat(chat_id):
    with pytest.raises(ValueError):
        FirestoreCvReviewStore(Client()).create_or_resume('offer', chat_id, CvVersionKey('offer', 'r', 'p', 'j'))


def test_review_and_revision_identifiers_fit_callbacks():
    import re
    store = FirestoreCvReviewStore(Client())
    review = store.create_or_resume('offer', '123', CvVersionKey('offer', 'r', 'p', 'j'))
    revision = store.publish_revision(review.review_id, None, 'gs://private/a.md', None)
    assert re.fullmatch(r'[A-Za-z0-9_-]{16}', review.review_id)
    assert re.fullmatch(r'[A-Za-z0-9_-]{16}', revision.revision_id)


def test_expired_generation_worker_cannot_publish_or_release_successor():
    from datetime import datetime, timezone, timedelta
    client = Client()
    store = FirestoreCvReviewStore(client)
    review = store.create_or_resume('offer', '123', CvVersionKey('offer', 'r', 'p', 'j'))
    store.save_context(review.review_id, {'resume_text': 'original'})
    first = store.claim_generation(review.review_id)
    lease = store.reviews.document(review.review_id).collection('context').document('generation')
    lease.set({'token': first, 'expires_at': datetime.now(timezone.utc) - timedelta(seconds=1)})
    second = store.claim_generation(review.review_id)
    store.release_generation(review.review_id, first)
    with pytest.raises(StaleCvRevision, match='superseded'):
        store.publish_generated(review.review_id, first, 'gs://private/old.md', {'match': 'old'})
    revision = store.publish_generated(review.review_id, second, 'gs://private/new.md', {'match': 'new'})
    assert store.load_context(review.review_id) == {'match': 'new', 'revision_id': revision.revision_id}
    assert store.load(review.review_id, '123').active_revision_id == revision.revision_id
    store.save_context(review.review_id, {'match': 'late'})
    assert store.load_context(review.review_id)['match'] == 'new'


def test_failed_generated_context_write_rolls_back_revision_and_allows_retry(monkeypatch):
    client = Client()
    store = FirestoreCvReviewStore(client)
    review = store.create_or_resume('offer', '123', CvVersionKey('offer', 'r', 'p', 'j'))
    store.save_context(review.review_id, {'resume_text': 'original'})
    token = store.claim_generation(review.review_id)
    original = store.publish_revision_in_transaction
    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('publication failure')
    monkeypatch.setattr(store, 'publish_revision_in_transaction', failed)
    with pytest.raises(RuntimeError):
        store.publish_generated(review.review_id, token, 'gs://private/old.md', {'match': 'old'})
    assert store.load(review.review_id, '123').active_revision_id is None
    assert store.load_context(review.review_id) == {'resume_text': 'original'}
    store.release_generation(review.review_id, token)
    assert store.claim_generation(review.review_id) != token
