"""Wire the collector without importing the agent or its runtime dependencies."""

from __future__ import annotations

import os

from google.auth.exceptions import DefaultCredentialsError
from google.cloud import firestore

from .adapters.firestore_store import FirestoreCollectorStore
from .adapters.linkedin_mcp_job_source import LinkedInMcpJobSource, McpServerParams
from .collect_jobs import CollectJobs
from .config import CollectorConfig


def build_collector(config: CollectorConfig) -> CollectJobs:
    project = os.environ.get("FIRESTORE_PROJECT_ID", "").strip()
    if not project:
        raise ValueError("FIRESTORE_PROJECT_ID no está configurado")
    linkedin = config.search.sources.linkedin
    if not linkedin.enabled:
        raise ValueError("La fuente LinkedIn está desactivada")
    try:
        client = firestore.Client(project=project)
    except DefaultCredentialsError as exc:
        raise ValueError("No se encontraron credenciales de Firestore (GOOGLE_APPLICATION_CREDENTIALS)") from exc
    server = McpServerParams(name="linkedin", command=linkedin.command, args=linkedin.args, env=linkedin.env)
    source = LinkedInMcpJobSource(server, max_pages=linkedin.max_pages)
    return CollectJobs(source, FirestoreCollectorStore(client))
