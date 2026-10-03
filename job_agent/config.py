"""Configuration loading (config.yaml + environment variables). Consumed only by the composition root."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from job_contracts import SearchPreferences

from .domain.policies import SearchBudgets

log = logging.getLogger(__name__)

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
    """Budgets and sources only; the candidate's search preferences live in Firestore."""

    max_roles_from_profile: int = 3
    max_queries: int = 8
    max_details_per_run: int = 20
    sources: SourcesConfig = Field(default_factory=SourcesConfig)


class MatchingConfig(BaseModel):
    min_score_to_notify: int = Field(default=70, ge=0, le=100)


class OpenRouterConfig(BaseModel):
    base_url: str = "https://openrouter.ai/api/v1"
    reasoning: bool = False  # send the task's effort as OpenRouter's `reasoning.effort` (reasoning models)


class LlmConfig(BaseModel):
    """Which provider and model(s) the LLM tasks use."""

    provider: Literal["anthropic", "openrouter"] = "anthropic"
    model: str | None = None  # default for every task; anthropic falls back to claude-opus-5-5
    models: dict[Literal["profile", "match", "tailor", "preferences"], str] = Field(default_factory=dict)
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
    llm: LlmConfig = Field(default_factory=LlmConfig)
    profile_refresh_days: int = 30
    language: str = "es"

    def search_budgets(self) -> SearchBudgets:
        return SearchBudgets(
            max_roles_from_profile=self.search.max_roles_from_profile,
            max_queries=self.search.max_queries,
            max_details_per_run=self.search.max_details_per_run,
        )

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
        if value:  # a blank placeholder (KEY=) means "not set"
            os.environ.setdefault(key, value)


LEGACY_SEARCH_KEYS = (
    "locations", "extra_keywords", "posted_within_days", "work_types", "experience_levels",
    "exclude_companies", "exclude_title_keywords",
)


def _config_path(path: str | os.PathLike | None) -> Path:
    return Path(path or os.environ.get("JOB_AGENT_CONFIG", ROOT / "config.yaml"))


def _read_yaml(path: Path) -> dict:
    return (yaml.safe_load(path.read_text()) if path.exists() else None) or {}


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = _config_path(path)
    raw = _read_yaml(path)
    search = raw.get("search")
    legacy = [key for key in LEGACY_SEARCH_KEYS if isinstance(search, dict) and key in search]
    if legacy:  # names only: the values may be private
        log.warning(
            "Ignoring search keys in %s now stored in Firestore: %s. "
            "Run `python -m job_agent seed-search-preferences --from-config %s` once and remove them.",
            path, ", ".join(legacy), path,
        )
    return Config.model_validate(_expand_env(raw))


def legacy_search_preferences(path: Path) -> SearchPreferences:
    """Build preferences from the legacy ``search`` keys of a YAML file (one-time migration)."""
    search = _read_yaml(Path(path)).get("search")
    search = search if isinstance(search, dict) else {}
    if not any(key in search for key in LEGACY_SEARCH_KEYS):
        raise ValueError(f"{path} no contiene claves de búsqueda para migrar ({', '.join(LEGACY_SEARCH_KEYS)})")
    data = {key: search[key] for key in LEGACY_SEARCH_KEYS if key in search and key != "extra_keywords"}
    if "extra_keywords" in search:
        data["keywords_include"] = search["extra_keywords"]
    return SearchPreferences.model_validate(data)


def require_firebase_storage_bucket() -> str:
    """Require the webhook's private bucket without affecting the local CLI flow."""
    bucket = os.environ.get('FIREBASE_STORAGE_BUCKET', '')
    if not bucket.strip() or '/' in bucket or any(c.isspace() for c in bucket):
        raise ValueError('FIREBASE_STORAGE_BUCKET must be a nonempty bare bucket name')
    return bucket
