"""Composition root for the LLM models: the only place that knows which provider implements each task."""

from __future__ import annotations

import os

from .adapters.llm import AnthropicStructuredModel, OpenRouterStructuredModel, StructuredModel
from .config import LlmConfig


def build_llm(llm: LlmConfig, task: str, cache: dict[str, StructuredModel] | None = None) -> StructuredModel:
    """Model for one LLM task (profile, match, tailor); tasks sharing a model share the instance."""
    cache = {} if cache is None else cache
    model = llm.model_for(task)
    key = f"{llm.provider}:{model}"
    if key not in cache:
        if llm.provider == "openrouter":
            cache[key] = OpenRouterStructuredModel(
                model or "",
                os.environ.get("OPENROUTER_API_KEY", ""),
                base_url=llm.openrouter.base_url,
                reasoning=llm.openrouter.reasoning,
            )
        else:
            cache[key] = AnthropicStructuredModel(model) if model else AnthropicStructuredModel()
    return cache[key]
