"""Configuration loading (config.yaml + environment variables). Consumed only by the composition root."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class RepoSpec(BaseModel):
    url: str
    branch: str | None = None


class LinkedInSourceConfig(BaseModel):
    """LinkedIn through its MCP server, queried directly (no LLM)."""

    enabled: bool = True
    command: str = "uvx"
    args: list[str] = Field(default_factory=lambda: ["mcp-server-linkedin@latest"])
    env: dict[str, str] = Field(default_factory=dict)
    max_pages: int = 2


class SourcesConfig(BaseModel):
    linkedin: LinkedInSourceConfig = Field(default_factory=LinkedInSourceConfig)


class SearchConfig(BaseModel):
    # Search plan (deterministic): keywords x locations
    locations: list[str] = Field(default_factory=lambda: ["Remote"])
    extra_keywords: list[str] = Field(default_factory=list)
    max_roles_from_profile: int = 3
    max_queries: int = 8
    # Filters applied by the job board itself
    posted_within_days: int = 1
    work_types: list[str] = Field(default_factory=list)
    experience_levels: list[str] = Field(default_factory=list)
    # Filters applied by the agent before any LLM call
    exclude_companies: list[str] = Field(default_factory=list)
    exclude_title_keywords: list[str] = Field(default_factory=list)
    # Budgets
    max_details_per_run: int = 40
    max_jobs_per_run: int = 25
    sources: SourcesConfig = Field(default_factory=SourcesConfig)


class MatchingConfig(BaseModel):
    min_score_to_notify: int = 70
    min_score_to_tailor: int = 70


class ResumeReuseConfig(BaseModel):
    enabled: bool = True
    max_candidates: int = 20


class OpenRouterConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    reasoning: bool = False  # send the task's effort as OpenRouter's `reasoning.effort` (reasoning models)


class LlmConfig(BaseModel):
    """Which provider and model(s) the LLM tasks use."""

    provider: Literal["anthropic", "openrouter"] = "anthropic"
    model: str | None = None  # default for every task; anthropic falls back to claude-opus-5-5
    models: dict[Literal["profile", "match", "select", "tailor"], str] = Field(default_factory=dict)
    openrouter: OpenRouterConfig = Field(default_factory=OpenRouterConfig)

    def model_for(self, task: str) -> str | None:
        return self.models.get(task) or os.environ.get("JOB_AGENT_MODEL") or self.model


class Config(BaseModel):
    resume_path: str = "resume/base.md"
    github_user: str | None = None
    include_forks: bool = False
    max_repos: int = 15
    repositories: list[RepoSpec] = Field(default_factory=list)
    search: SearchConfig = Field(default_factory=SearchConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    resume_reuse: ResumeReuseConfig = Field(default_factory=ResumeReuseConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    profile_refresh_days: int = 7
    language: str = "es"

    @property
    def resume_file(self) -> Path:
        return ROOT / self.resume_path


_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_dotenv(path: str | os.PathLike | None = None) -> None:
    """Load KEY=value lines from ``.env`` into the environment (local runs). Existing variables win."""
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("JOB_AGENT_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Config.model_validate(_expand_env(raw or {}))
