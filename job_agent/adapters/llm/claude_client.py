"""Shared Claude (Anthropic SDK) client used by every LLM-backed adapter."""

from __future__ import annotations

import logging
import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
MODEL = os.environ.get("JOB_AGENT_MODEL", DEFAULT_MODEL)
# Server-side refusal fallback: re-runs a declined request on Anthropic's recommended model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None
_async_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def async_client() -> anthropic.AsyncAnthropic:
    global _async_client
    if _async_client is None:
        _async_client = anthropic.AsyncAnthropic()
    return _async_client


class LLMError(RuntimeError):
    pass


def structured(
    *,
    system: str,
    content: str | list,
    schema: type[T],
    effort: str = "medium",
    max_tokens: int = 16000,
) -> T:
    """One request whose answer is validated against ``schema``."""
    response = client().beta.messages.parse(
        model=MODEL,
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
