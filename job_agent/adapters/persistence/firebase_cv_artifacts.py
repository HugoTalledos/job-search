"""Private CV artifacts in Cloud Storage for Firebase, using ADC by default."""

from __future__ import annotations

import re

from google.cloud import storage

from ...application.cv_models import CvArtifacts, CvVersionKey


class FirebaseCvArtifactStore:
    def __init__(self, bucket_name: str, client: storage.Client | None = None) -> None:
        if not bucket_name.strip() or '/' in bucket_name or any(c.isspace() for c in bucket_name):
            raise ValueError('FIREBASE_STORAGE_BUCKET must be a nonempty bare bucket name')
        self.bucket_name = bucket_name
        self.bucket = (client if client is not None else storage.Client()).bucket(bucket_name)

    def save(
        self, key: CvVersionKey, pdf: bytes, markdown: str, readme: str, *, attempt_id: str,
    ) -> CvArtifacts:
        """Upload a claimed, not-yet-ready version; return only after all three succeed.

        Each claim owns an immutable attempt directory. A later claimed retry uses
        its new token, so uploads from an expired worker cannot overwrite READY bytes.
        A partial failure leaves private objects unreferenced; only the winning claim
        can publish its URIs through mark_ready. Existing objects are never overwritten.
        No ACLs or download tokens are added; deployment must supply a private bucket.
        """
        if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', attempt_id):
            raise ValueError('attempt_id must be a nonempty safe path component')
        if not pdf:
            raise ValueError('A nonempty PDF is required')
        prefix = f'cvs/{key.posting_id}/{key.version_id}/{attempt_id}'
        for name, content, content_type in (
            ('cv.pdf', pdf, 'application/pdf'),
            ('resume.md', markdown, 'text/markdown; charset=utf-8'),
            ('README.md', readme, 'text/markdown; charset=utf-8'),
        ):
            self.bucket.blob(f'{prefix}/{name}').upload_from_string(
                content, content_type=content_type, if_generation_match=0,
            )
        uri = f'gs://{self.bucket_name}/{prefix}'
        return CvArtifacts(f'{uri}/cv.pdf', f'{uri}/resume.md', f'{uri}/README.md')

    def read_pdf(self, artifacts: CvArtifacts) -> bytes:
        prefix = f'gs://{self.bucket_name}/'
        if not artifacts.pdf_uri.startswith(prefix) or not artifacts.pdf_uri[len(prefix):]:
            raise ValueError('CV artifact must belong to the configured private bucket')
        return self.bucket.blob(artifacts.pdf_uri[len(prefix):]).download_as_bytes()

    def save_markdown(self, review_id: str, revision_id: str, markdown: str) -> str:
        for value in (review_id, revision_id):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
                raise ValueError('Review and revision IDs must be safe path components')
        path = f'cv_reviews/{review_id}/{revision_id}/resume.md'
        self.bucket.blob(path).upload_from_string(
            markdown, content_type='text/markdown; charset=utf-8', if_generation_match=0,
        )
        return f'gs://{self.bucket_name}/{path}'

    def read_markdown(self, uri: str) -> str:
        prefix = f'gs://{self.bucket_name}/'
        if not uri.startswith(prefix) or not uri[len(prefix):]:
            raise ValueError('CV artifact must belong to the configured private bucket')
        content = self.bucket.blob(uri[len(prefix):]).download_as_bytes()
        return content.decode('utf-8') if isinstance(content, bytes) else content
