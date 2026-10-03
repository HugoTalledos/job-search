import pytest
from google.cloud import firestore


class Snapshot:
    def __init__(self, doc_id, data):
        self.id, self.data = doc_id, data
        self.exists = data is not None

    def to_dict(self):
        return self.data


class Document:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def set(self, data):
        if self.client.error:
            raise self.client.error
        self.client.docs[self.path] = data

    def get(self):
        if self.client.error:
            raise self.client.error
        return Snapshot(self.path.rsplit('/', 1)[-1], self.client.docs.get(self.path))


class Collection:
    def __init__(self, client, name, url=None, count=None):
        self.client, self.name, self.url, self.count = client, name, url, count

    def document(self, doc_id):
        return Document(self.client, f'{self.name}/{doc_id}')

    def where(self, *, filter):
        assert filter.field_path == 'job.url'
        assert filter.op_string == '=='
        self.client.queries.append((self.name, filter.field_path, filter.op_string, filter.value))
        return Collection(self.client, self.name, filter.value, self.count)

    def limit(self, count):
        return Collection(self.client, self.name, self.url, count)

    def stream(self):
        if self.client.error:
            raise self.client.error
        matched = [Snapshot(path.split('/', 1)[1], data)
                   for path, data in self.client.docs.items()
                   if path.startswith(f'{self.name}/')
                   and (self.url is None or data.get('job', {}).get('url') == self.url)]
        yield from matched[:self.count]


class Client:
    def __init__(self):
        self.docs, self.queries = {}, []
        self.error = None

    def collection(self, name):
        return Collection(self, name)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def index(client):
    from job_agent.adapters.persistence.firestore_offer_messages import FirestoreOfferMessageIndex
    return FirestoreOfferMessageIndex(client)


def test_records_message_with_posting_id_and_server_timestamp(index, client):
    from job_agent.scoring.models import TelegramMessageRef

    index.record(TelegramMessageRef('42', 91), 'offer')
    assert client.docs == {'telegram_offer_messages/42_91': {
        'posting_id': 'offer', 'sent_at': firestore.SERVER_TIMESTAMP,
    }}
    assert index.resolve('42', 91) == 'offer'


def test_two_receipts_can_point_to_one_posting(index, client):
    from job_agent.scoring.models import TelegramMessageRef

    index.record(TelegramMessageRef('42', 91), 'offer')
    index.record(TelegramMessageRef('42', 92), 'offer')
    assert len(client.docs) == 2
    assert index.resolve('42', 91) == index.resolve('42', 92) == 'offer'
    assert index.resolve('43', 91) is None


def test_missing_message_returns_none(index):
    assert index.resolve('42', 91) is None


@pytest.mark.parametrize('data', [{}, {'posting_id': None}, {'posting_id': 7}, {'posting_id': ''}])
def test_malformed_message_record_does_not_resolve(index, client, data):
    client.docs['telegram_offer_messages/42_91'] = data
    assert index.resolve('42', 91) is None


def test_unique_legacy_url_resolves_exact_posting(index, client):
    client.docs['job_postings/offer'] = {'job': {'url': 'https://jobs.example/one'}}
    client.docs['job_postings/other'] = {'job': {'url': 'https://jobs.example/other'}}
    assert index.resolve_unique_url('https://jobs.example/one') == 'offer'
    assert client.queries == [('job_postings', 'job.url', '==', 'https://jobs.example/one')]


def test_ambiguous_legacy_url_is_rejected(index, client):
    client.docs['job_postings/first'] = {'job': {'url': 'https://jobs.example/one'}}
    client.docs['job_postings/second'] = {'job': {'url': 'https://jobs.example/one'}}
    assert index.resolve_unique_url('https://jobs.example/one') is None


def test_unknown_legacy_url_is_rejected(index):
    assert index.resolve_unique_url('https://jobs.example/missing') is None


@pytest.mark.parametrize('url', ['', ' '])
def test_absent_legacy_url_does_not_query_or_resolve(index, client, url):
    client.docs['job_postings/offer'] = {'job': {'url': url}}
    assert index.resolve_unique_url(url) is None
    assert client.queries == []


@pytest.mark.parametrize('operation', ['record', 'resolve', 'resolve_unique_url'])
def test_firestore_errors_propagate(index, client, operation):
    from job_agent.scoring.models import TelegramMessageRef
    client.error = RuntimeError('Firestore unavailable')
    with pytest.raises(RuntimeError, match='Firestore unavailable'):
        if operation == 'record':
            index.record(TelegramMessageRef('42', 91), 'offer')
        elif operation == 'resolve':
            index.resolve('42', 91)
        else:
            index.resolve_unique_url('https://jobs.example/one')
