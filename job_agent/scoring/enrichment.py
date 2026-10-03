"""Extract explicit offer details with a separate Jev Choice decision."""

from __future__ import annotations

import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from job_contracts import JobPosting

from .jev import DECISIONS_URL, MODEL
from .models import PostingEnrichment

_LANGUAGE = re.compile(r"\b(?:English|ingl[eé]s|Spanish|espa[nñ]ol|Portuguese|portugu[eé]s|French|franc[eé]s)\b", re.I)
_SALARY_RANGE = re.compile(
    r"(?:\$|€|\b(?:USD|COP|EUR)\b)\s*\d[\d.,]*\s*(?:-|–|—|to|a)\s*(?:\$|€|\b(?:USD|COP|EUR)\b)?\s*\d[\d.,]*",
    re.I,
)
_SENTENCE = re.compile(r"\n+|(?<=[.!?])\s+")
_MAX_CANDIDATES = 8
_MIN_CONFIDENCE = 0.8


class _ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float = Field(ge=0, le=1, strict=True)


class _DecisionResponse(BaseModel):
    answers: dict[str, _ChoiceAnswer]


def _candidates(description: str, pattern: re.Pattern[str]) -> list[str]:
    found: list[str] = []
    for piece in _SENTENCE.split(description):
        span = piece.strip()
        match = pattern.search(span) if span else None
        if match:
            start = max(0, match.start() - 60)
            candidate = span[start:start + 180]
            if candidate in found:
                continue
            found.append(candidate)
            if len(found) == _MAX_CANDIDATES:
                break
    return found


class JevOfferEnricher:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key.strip():
            raise ValueError("OPENROUTER_API_KEY no está configurada")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    def enrich(self, job: JobPosting) -> PostingEnrichment:
        language = _candidates(job.description, _LANGUAGE)
        salary = _candidates(job.description, _SALARY_RANGE)
        available = {key: candidates for key, candidates in (
            ("required_language", language), ("salary_range", salary)
        ) if candidates}
        if not available:
            return PostingEnrichment()

        questions = {
            key: {
                "type": "choice",
                "instructions": (
                    "¿Cuál fragmento expresa un idioma obligatorio para esta oferta?"
                    if key == "required_language" else
                    "¿Cuál fragmento expresa el rango salarial ofrecido por esta oferta?"
                ),
                "criteria": {
                    **{f"candidate_{index}": span for index, span in enumerate(candidates)},
                    "none": "Ningún candidato describe este campo para esta oferta.",
                },
            }
            for key, candidates in available.items()
        }
        response = self.client.post(
            DECISIONS_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": MODEL, "state": {"description": job.description}, "questions": questions},
        )
        response.raise_for_status()
        answers = _DecisionResponse.model_validate(response.json()).answers
        selected: dict[str, str | None] = {"required_language": None, "salary_range": None}
        for key, candidates in available.items():
            answer = answers.get(key)
            if answer is None:
                raise ValueError(f"Missing Jev answer for {key}")
            if answer.choice == "none":
                continue
            options = {f"candidate_{index}": span for index, span in enumerate(candidates)}
            if answer.choice not in options:
                raise ValueError(f"Unknown Jev candidate for {key}")
            span = options[answer.choice]
            if span not in job.description:
                raise ValueError(f"Jev candidate not in description for {key}")
            if answer.confidence >= _MIN_CONFIDENCE:
                selected[key] = span
        return PostingEnrichment(**selected)
