"""Driven adapters for the LLM-backed ports (profile, matching, resume reuse and tailoring).

Prompts are provider-neutral (``prompts``); a ``StructuredModel`` provider does the transport:
``AnthropicStructuredModel`` (Claude) or ``OpenRouterStructuredModel`` (any model on OpenRouter).
"""

from .anthropic_model import AnthropicStructuredModel
from .openrouter_model import OpenRouterStructuredModel
from .structured import LLMError, StructuredModel
from .tasks import LlmJobMatcher, LlmProfileInferer, LlmResumeSelector, LlmResumeTailor

__all__ = [
    "AnthropicStructuredModel",
    "LLMError",
    "LlmJobMatcher",
    "LlmProfileInferer",
    "LlmResumeSelector",
    "LlmResumeTailor",
    "OpenRouterStructuredModel",
    "StructuredModel",
]
