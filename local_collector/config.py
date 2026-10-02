"""Only the configuration needed to run the local LinkedIn collector."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand_env(value):
    if isinstance(value, str):
        return _ENV_RE.sub(lambda match: os.environ.get(match.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


class LinkedInConfig(BaseModel):
    enabled: bool = True
    command: str = "uvx"
    args: list[str] = Field(default_factory=lambda: ["mcp-server-linkedin@latest"])
    env: dict[str, str] = Field(default_factory=dict)
    max_pages: int = 2


class SourcesConfig(BaseModel):
    linkedin: LinkedInConfig = Field(default_factory=LinkedInConfig)


class SearchConfig(BaseModel):
    sources: SourcesConfig = Field(default_factory=SourcesConfig)


class CollectorConfig(BaseModel):
    search: SearchConfig = Field(default_factory=SearchConfig)


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
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
        if value:
            os.environ.setdefault(key, value)


def load_config(path: str | None = None) -> CollectorConfig:
    config_path = Path(path or os.environ.get("JOB_AGENT_CONFIG", ROOT / "config.yaml"))
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    search = (raw or {}).get("search") or {}
    linkedin = (search.get("sources") or {}).get("linkedin") or {}
    linkedin = _expand_env(linkedin)
    return CollectorConfig.model_validate({"search": {"sources": {"linkedin": linkedin}}})
