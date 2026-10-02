"""StructuredModel on the Anthropic API (Claude)."""

from __future__ import annotations

import logging

import anthropic

from .structured import Effort, LLMError, T

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
# Server-side refusal fallback: re-runs a declined request on Anthropic's recommended model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicStructuredModel:
    def __init__(self, model: str = DEFAULT_MODEL, client: anthropic.Anthropic | None = None) -> None:
        self.model = model
        self._client = client

    @property
    def name(self) -> str:
        return f"anthropic/{self.model}"

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def complete(
        self,
        *,
        system: str,
        content: list[dict],
        schema: type[T],
        effort: Effort = "medium",
        max_tokens: int = 16000,
    ) -> T:
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            output_format=schema,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            details = response.stop_details
            raise LLMError(f"Model refused ({details.category if details else 'unknown'}): request {response._request_id}")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"Output truncated at max_tokens={max_tokens}: request {response._request_id}")
        if response.parsed_output is None:
            raise LLMError(f"No structured output returned: request {response._request_id}")
        return response.parsed_output
