"""Jev's typed Score decision, independent of the legacy chat model."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from job_contracts import JobPosting
from job_agent.domain.models import Profile

from .models import ScoreResult

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
CRITERIA = [
    "Cargo ajeno al perfil o requisito indispensable incumplido.",
    "Pocos requisitos coincidentes y varias brechas esenciales.",
    "Encaje con el cargo y algunos requisitos, pero con una brecha importante.",
    "Mayoría de requisitos esenciales cumplidos y solo brechas menores.",
    "Requisitos esenciales prácticamente completos con evidencia sólida en el perfil.",
]


class _ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(ge=0, le=4)
    confidence: float = Field(ge=0, le=1)


class _Answers(BaseModel):
    affinity: _ScoreAnswer


class _DecisionResponse(BaseModel):
    answers: _Answers


class JevScoringTool:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key.strip():
            raise ValueError("OPENROUTER_API_KEY no está configurada")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=30)

    def score(self, profile: Profile, job: JobPosting) -> ScoreResult:
        response = self.client.post(
            DECISIONS_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": MODEL,
                "state": {"profile": profile.model_dump(), "job": job.model_dump()},
                "questions": {
                    "affinity": {
                        "type": "score",
                        "instructions": "¿Qué tan bien cumple el perfil los requisitos esenciales de esta oferta?",
                        "criteria": CRITERIA,
                    }
                },
            },
        )
        response.raise_for_status()
        answer = _DecisionResponse.model_validate(response.json()).answers.affinity
        normalized = int((Decimal(str(answer.score)) * 25).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return ScoreResult(score=normalized, confidence=answer.confidence, model=MODEL)
