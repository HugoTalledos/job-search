"""Configuration loading (config.yaml + environment variables). Consumed only by the composition root."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class RepoSpec(BaseModel):
    url: str
    branch: str | None = None


class McpServerSpec(BaseModel):
    """A job-board MCP server; each one becomes an independent JobSource adapter."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class SearchConfig(BaseModel):
    locations: list[str] = Field(default_factory=lambda: ["Remote"])
    extra_keywords: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    posted_within_days: int = 2
    max_jobs_per_run: int = 25
    web_search: bool = False
    web_search_domains: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerSpec] = Field(default_factory=list)


class MatchingConfig(BaseModel):
    min_score_to_notify: int = 70
    min_score_to_tailor: int = 70


class ResumeReuseConfig(BaseModel):
    enabled: bool = True
    max_candidates: int = 20


class Config(BaseModel):
    resume_path: str = "resume/base.md"
    github_user: str | None = None
    include_forks: bool = False
    max_repos: int = 15
    repositories: list[RepoSpec] = Field(default_factory=list)
    search: SearchConfig = Field(default_factory=SearchConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    resume_reuse: ResumeReuseConfig = Field(default_factory=ResumeReuseConfig)
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


def load_config(path: str | os.PathLike | None = None) -> Config:
    path = Path(path or os.environ.get("JOB_AGENT_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return Config.model_validate(_expand_env(raw or {}))
