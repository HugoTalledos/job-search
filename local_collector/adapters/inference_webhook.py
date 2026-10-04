"""Ask the remote agent to evaluate and notify the postings this collector stored."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

INFERENCE_PATH = "/webhooks/inference"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class InferenceWebhookTrigger:
    def __init__(self, base_url: str, api_key: str, client: httpx.Client | None = None) -> None:
        base_url = base_url.strip().rstrip("/")
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("JOB_AGENT_URL debe ser una URL http(s) válida")
        if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
            raise ValueError("JOB_AGENT_URL debe usar HTTPS (HTTP solo se permite en localhost)")
        if parts.query or parts.fragment:
            raise ValueError("JOB_AGENT_URL no debe incluir parámetros ni fragmentos")
        if not api_key.strip():
            raise ValueError("JOB_AGENT_WEBHOOK_API_KEY no está configurada")
        self.url = base_url + INFERENCE_PATH
        self.api_key = api_key.strip()
        self.client = client or httpx.Client(timeout=60)

    def trigger(self) -> None:
        try:
            response = self.client.post(self.url, headers={"X-API-Key": self.api_key})
        except httpx.HTTPError as exc:
            raise RuntimeError(f"no se pudo contactar {self.url} ({type(exc).__name__})") from exc
        if response.status_code == 401:
            raise RuntimeError("el servicio rechazó JOB_AGENT_WEBHOOK_API_KEY (401)")
        if response.status_code != 200:
            raise RuntimeError(f"el servicio respondió {response.status_code}")
