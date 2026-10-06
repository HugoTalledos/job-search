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
