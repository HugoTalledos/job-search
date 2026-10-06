import pytest
from google.api_core.exceptions import PreconditionFailed


class Blob:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def upload_from_string(self, data, *, content_type, if_generation_match=None):
        if self.name.endswith(self.client.fail_on or '!never!'):
            raise RuntimeError('upload failed')
        if if_generation_match == 0 and self.name in self.client.objects:
            raise PreconditionFailed('Object already exists')
        self.client.objects[self.name] = (data, content_type)

    def download_as_bytes(self):
        return self.client.objects[self.name][0]


class Client:
    def __init__(self):
        self.objects, self.fail_on = {}, None

    def bucket(self, name):
        assert name == 'private-bucket'
        return self

    def blob(self, name):
        return Blob(self, name)


@pytest.fixture
def key():
    from job_agent.application.cv_models import CvVersionKey
    return CvVersionKey('offer', 'resume', 'profile', 'job')


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def store(client):
    from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
    return FirebaseCvArtifactStore('private-bucket', client=client)


def test_artifact_paths_are_private_and_uploads_have_correct_types(store, client, key):
    artifacts = store.save(key, b'%PDF-1.7 test', '# CV', '# Readme', attempt_id='first')
    prefix = f'cvs/offer/{key.version_id}/first'
    assert artifacts.pdf_uri == f'gs://private-bucket/{prefix}/cv.pdf'
    assert artifacts.markdown_uri == f'gs://private-bucket/{prefix}/resume.md'
    assert artifacts.readme_uri == f'gs://private-bucket/{prefix}/README.md'
    assert client.objects == {
        f'{prefix}/cv.pdf': (b'%PDF-1.7 test', 'application/pdf'),
        f'{prefix}/resume.md': ('# CV', 'text/markdown; charset=utf-8'),
        f'{prefix}/README.md': ('# Readme', 'text/markdown; charset=utf-8'),
    }
    assert store.read_pdf(artifacts) == b'%PDF-1.7 test'


@pytest.mark.parametrize('failed_name', ['resume.md', 'README.md'])
def test_partial_upload_raises_and_new_attempt_uses_its_own_paths(store, client, key, failed_name):
    client.fail_on = failed_name
    with pytest.raises(RuntimeError, match='upload failed'):
        store.save(key, b'pdf', 'cv', 'readme', attempt_id='first')
    assert len(client.objects) < 3
    partial_objects = dict(client.objects)
    client.fail_on = None
    artifacts = store.save(key, b'pdf retry', 'cv', 'readme', attempt_id='second')
    assert len(client.objects) == len(partial_objects) + 3
    assert all(client.objects[name] == value for name, value in partial_objects.items())
    assert artifacts.pdf_uri.endswith('/second/cv.pdf')
    assert store.read_pdf(artifacts) == b'pdf retry'


@pytest.mark.parametrize('bucket', ['', '  ', 'gs://bucket', 'bucket/path'])
def test_invalid_bucket_is_rejected(bucket):
    from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
    with pytest.raises(ValueError):
        FirebaseCvArtifactStore(bucket, client=Client())


def test_empty_pdf_cannot_produce_complete_artifacts(store, client, key):
    with pytest.raises(ValueError):
        store.save(key, b'', '# cv', '# readme', attempt_id='first')
    assert not client.objects


@pytest.mark.parametrize('uri', ['https://public.example/cv.pdf', 'gs://other-bucket/cv.pdf'])
def test_read_pdf_rejects_public_or_foreign_bucket_uri(store, uri):
    from job_agent.application.cv_models import CvArtifacts
    with pytest.raises(ValueError):
        store.read_pdf(CvArtifacts(uri, '', ''))


def test_attempt_objects_cannot_be_overwritten_even_with_same_token(store, client, key):
    artifacts = store.save(key, b'original', 'original cv', 'original readme', attempt_id='first')
    original_objects = dict(client.objects)
    with pytest.raises(PreconditionFailed):
        store.save(key, b'replacement', 'new cv', 'new readme', attempt_id='first')
    assert client.objects == original_objects
    assert store.read_pdf(artifacts) == b'original'


@pytest.mark.parametrize('attempt_id', ['', ' ', '../other', 'nested/path', '.', '..', None])
def test_attempt_id_must_be_a_nonempty_safe_path_component(store, client, key, attempt_id):
    with pytest.raises(ValueError, match='attempt_id'):
        store.save(key, b'pdf', 'cv', 'readme', attempt_id=attempt_id)
    assert not client.objects


def test_review_markdown_is_immutable_and_legacy_markdown_is_readable(store, client, key):
    uri = store.save_markdown('review', 'revision', '# español')
    assert store.read_markdown(uri) == '# español'
    with pytest.raises(PreconditionFailed):
        store.save_markdown('review', 'revision', 'replacement')
    artifacts = store.save(key, b'pdf', '# legacy', 'readme', attempt_id='legacy')
    assert store.read_markdown(artifacts.markdown_uri) == '# legacy'


def test_failed_markdown_upload_returns_no_pointer(store, client):
    client.fail_on = 'resume.md'
    with pytest.raises(RuntimeError):
        store.save_markdown('review', 'revision', 'cv')
    assert not client.objects


@pytest.mark.parametrize('uri', ['gs://other/cv.md', 'https://public/cv.md', 'gs://private-bucket/'])
def test_markdown_read_requires_private_bucket(store, uri):
    with pytest.raises(ValueError):
        store.read_markdown(uri)
