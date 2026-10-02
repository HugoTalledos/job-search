"""The single capability every LLM task needs: one request whose answer is validated against a schema.

Internal to the LLM adapters: the task adapters (profile, matcher, selector, tailor) depend on this
protocol and each provider (Anthropic, OpenRouter...) implements it.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Effort = Literal["low", "medium", "high"]


class LLMError(RuntimeError):
    pass


class StructuredModel(Protocol):
    @property
    def name(self) -> str:
        """Provider and model, for logs."""
        ...

    def complete(
        self,
        *,
        system: str,
        content: list[dict],
        schema: type[T],
        effort: Effort = "medium",
        max_tokens: int = 16000,
    ) -> T: ...
