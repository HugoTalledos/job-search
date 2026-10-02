"""StructuredModel on OpenRouter (OpenAI-compatible chat completions API, any model it routes to).

Structured output strategy:
1. ``response_format: json_schema`` (strict) and ``provider.require_parameters`` so OpenRouter only
   routes to endpoints that support it;
2. if the model has no such endpoint, fall back (and remember it for this model) to describing the
   JSON schema in the prompt;
3. either way the answer is validated with Pydantic; an invalid answer is retried once with the
   validation error.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time

import httpx
from pydantic import BaseModel, ValidationError

from .structured import Effort, LLMError, T

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def strict_json_schema(schema: type[BaseModel]) -> dict:
    """Pydantic schema adapted to OpenAI-style strict mode: closed objects, every property required."""
    result = copy.deepcopy(schema.model_json_schema())

    def visit(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(result)
    return result


def _parse_json(text: str):
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    if not text.startswith(("{", "[")) and "{" in text:  # prose around the object
        text = text[text.index("{"): text.rindex("}") + 1]
    return json.loads(text)


class OpenRouterStructuredModel:
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        reasoning: bool = False,
        timeout: float = 300.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not model:
            raise ValueError("OpenRouter requires a model id, e.g. 'anthropic/<model>' (see openrouter.ai/models)")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not set")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.reasoning = reasoning
        self._prompt_mode = False  # set once the model proves not to support json_schema
        self._http = http_client or httpx.Client(timeout=timeout)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": "job-search agent",
        }

    @property
    def name(self) -> str:
        return f"openrouter/{self.model}"

    def complete(
        self,
        *,
        system: str,
        content: list[dict],
        schema: type[T],
        effort: Effort = "medium",
        max_tokens: int = 16000,
    ) -> T:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [self._part(block) for block in content]},
        ]
        text = self._request(messages, schema, effort, max_tokens)
        try:
            return schema.model_validate(_parse_json(text))
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            log.warning("[%s] invalid structured answer, retrying once: %s", self.name, str(exc)[:300])
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"That answer is not valid: {str(exc)[:1000]}\n"
                                            "Reply again with only the corrected JSON object."},
            ]
            text = self._request(messages, schema, effort, max_tokens)
            try:
                return schema.model_validate(_parse_json(text))
            except (json.JSONDecodeError, ValueError, ValidationError) as exc2:
                raise LLMError(f"{self.name} did not return valid {schema.__name__}: {str(exc2)[:300]}") from exc2

    @staticmethod
    def _part(block: dict) -> dict:
        part = {"type": "text", "text": block["text"]}
        if "cache_control" in block:  # honoured by providers with prompt caching (Anthropic, Gemini)
            part["cache_control"] = block["cache_control"]
        return part

    def _body(self, messages: list[dict], schema: type[BaseModel], effort: Effort, max_tokens: int) -> dict:
        body: dict = {"model": self.model, "messages": messages, "max_tokens": max_tokens}
        if self.reasoning:
            body["reasoning"] = {"effort": effort}
        if self._prompt_mode:
            instructions = (
                "\n\nRespond with a single JSON object, without markdown fences or any other text, that "
                "conforms to this JSON schema:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )
            body["messages"] = [{**messages[0], "content": messages[0]["content"] + instructions}, *messages[1:]]
        else:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_json_schema(schema)},
            }
            body["provider"] = {"require_parameters": True}
        return body

    def _request(self, messages: list[dict], schema: type[BaseModel], effort: Effort, max_tokens: int) -> str:
        for attempt in range(4):
            response = self._http.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers,
                json=self._body(messages, schema, effort, max_tokens),
            )
            if response.status_code in (400, 404) and not self._prompt_mode and self._unsupported(response):
                log.warning("[%s] no endpoint supports json_schema; describing the schema in the prompt", self.name)
                self._prompt_mode = True
                continue
            if response.status_code in _RETRY_STATUS and attempt < 3:
                wait = float(response.headers.get("retry-after", 2 ** (attempt + 1)))
                log.warning("[%s] HTTP %s, retrying in %.0fs", self.name, response.status_code, wait)
                time.sleep(min(wait, 60))
                continue
            if response.status_code != 200:
                raise LLMError(f"{self.name} HTTP {response.status_code}: {response.text[:500]}")
            data = response.json()
            if "error" in data:  # some upstream errors arrive with HTTP 200
                raise LLMError(f"{self.name} error: {json.dumps(data['error'])[:500]}")
            choice = (data.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise LLMError(f"{self.name}: output truncated at max_tokens={max_tokens}")
            text = (choice.get("message") or {}).get("content") or ""
            if not text.strip():
                raise LLMError(f"{self.name} returned an empty answer (finish_reason={choice.get('finish_reason')})")
            return text
        raise LLMError(f"{self.name}: gave up after repeated errors")

    @staticmethod
    def _unsupported(response: httpx.Response) -> bool:
        text = response.text.lower()
        return any(s in text for s in ("no endpoints found", "response_format", "json_schema", "structured output"))
