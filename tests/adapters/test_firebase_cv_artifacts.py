import pytest


class Blob:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def upload_from_string(self, data, *, content_type):
        if self.name.endswith(self.client.fail_on or '!never!'):
            raise RuntimeError('upload failed')
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
    artifacts = store.save(key, b'%PDF-1.7 test', '# CV', '# Readme')
    prefix = f'cvs/offer/{key.version_id}'
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
def test_partial_upload_raises_and_retry_uses_same_version_paths(store, client, key, failed_name):
    client.fail_on = failed_name
    with pytest.raises(RuntimeError, match='upload failed'):
        store.save(key, b'pdf', 'cv', 'readme')
    assert len(client.objects) < 3
    client.fail_on = None
    artifacts = store.save(key, b'pdf retry', 'cv', 'readme')
    assert len(client.objects) == 3
    assert store.read_pdf(artifacts) == b'pdf retry'


@pytest.mark.parametrize('bucket', ['', '  ', 'gs://bucket', 'bucket/path'])
def test_invalid_bucket_is_rejected(bucket):
    from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
    with pytest.raises(ValueError):
        FirebaseCvArtifactStore(bucket, client=Client())


def test_empty_pdf_cannot_produce_complete_artifacts(store, client, key):
    with pytest.raises(ValueError):
        store.save(key, b'', '# cv', '# readme')
    assert not client.objects


@pytest.mark.parametrize('uri', ['https://public.example/cv.pdf', 'gs://other-bucket/cv.pdf'])
def test_read_pdf_rejects_public_or_foreign_bucket_uri(store, uri):
    from job_agent.application.cv_models import CvArtifacts
    with pytest.raises(ValueError):
        store.read_pdf(CvArtifacts(uri, '', ''))
