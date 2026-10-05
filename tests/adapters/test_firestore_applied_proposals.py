from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.adapters.test_firestore_cv_tracking import Client


def test_marks_existing_posting_once_with_snapshot_and_server_time(job):
    from google.cloud import firestore
    from job_agent.adapters.persistence.firestore_applied_proposals import FirestoreAppliedProposals

    client = Client()
    client.docs['job_postings/offer'] = {'job': job.model_dump(mode='json')}
    store = FirestoreAppliedProposals(client)

    assert store.mark_applied('offer') is True
    first = client.docs['applied_proposals/offer']
    assert first['posting_ref'] == 'job_postings/offer'
    assert first['job'] == job.model_dump(mode='json')
    assert first['applied_at'] is firestore.SERVER_TIMESTAMP
    assert store.mark_applied('offer') is False
    assert client.docs['applied_proposals/offer'] == first


def test_missing_posting_is_not_marked():
    from job_agent.adapters.persistence.firestore_applied_proposals import FirestoreAppliedProposals

    client = Client()
    with pytest.raises(LookupError):
        FirestoreAppliedProposals(client).mark_applied('missing')
    assert client.docs == {}


def test_concurrent_presses_create_one_record(job):
    from job_agent.adapters.persistence.firestore_applied_proposals import FirestoreAppliedProposals

    client = Client()
    client.docs['job_postings/offer'] = {'job': job.model_dump(mode='json')}
    client.barrier = Barrier(2)
    client.barrier_on = lambda path: path == 'applied_proposals/offer'
    store = FirestoreAppliedProposals(client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.mark_applied('offer'), range(2)))

    assert sorted(results) == [False, True]
    assert client.conflicts == 1
