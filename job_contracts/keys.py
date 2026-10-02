"""Stable Firestore IDs for LinkedIn postings, which always have an external ID."""

import hashlib
import re
import unicodedata

from .models import JobLead, JobPosting


def _key(source: str, external_id: str) -> str:
    if not external_id:
        raise ValueError("A collected posting must have an external ID")
    ascii_source = unicodedata.normalize("NFKD", source).encode("ascii", "ignore").decode().lower()
    normalized_source = re.sub(r"[^a-z0-9]+", " ", ascii_source).strip()
    return hashlib.sha1(f"{normalized_source}:{external_id.strip()}".encode()).hexdigest()[:16]


def lead_key(lead: JobLead) -> str:
    return _key(lead.source, lead.external_id)


def job_key(job: JobPosting) -> str:
    return _key(job.source, job.external_id)
