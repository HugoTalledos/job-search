"""Collect evidence about the candidate from git repositories (GitHub or any other remote)."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

from .config import Config, RepoSpec

log = logging.getLogger(__name__)

MANIFESTS = (
    "package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "Dockerfile", "docker-compose.yml",
    "serverless.yml", "terraform.tf", "main.tf",
)
EXT_LANG = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".cpp": "C++", ".c": "C", ".swift": "Swift", ".scala": "Scala", ".sql": "SQL",
    ".tf": "Terraform", ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte", ".ipynb": "Jupyter",
}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", "target"}


@dataclass
class RepoDigest:
    url: str
    head: str
    text: str


def _authed(url: str) -> str:
    """Inject GITHUB_TOKEN for private GitHub repos (never logged)."""
    token = os.environ.get("REPO_READ_TOKEN") or os.environ.get("GITHUB_TOKEN")
    parsed = urlparse(url)
    if token and parsed.scheme == "https" and parsed.hostname == "github.com" and not parsed.username:
        return urlunparse(parsed._replace(netloc=f"x-access-token:{token}@{parsed.netloc}"))
    return url


def _git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=180
    ).stdout


def list_github_repos(user: str, include_forks: bool, limit: int) -> list[RepoSpec]:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("REPO_READ_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(
        f"https://api.github.com/users/{user}/repos",
        params={"sort": "pushed", "per_page": 100, "type": "owner"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    repos = [r for r in resp.json() if include_forks or not r.get("fork")]
    return [RepoSpec(url=r["clone_url"], branch=r.get("default_branch")) for r in repos[:limit]]


def resolve_repos(cfg: Config) -> list[RepoSpec]:
    specs = list(cfg.repositories)
    if cfg.github_user:
        try:
            specs += list_github_repos(cfg.github_user, cfg.include_forks, cfg.max_repos)
        except httpx.HTTPError as exc:
            log.warning("Could not list repos for %s: %s", cfg.github_user, exc)
    seen, unique = set(), []
    for spec in specs:
        key = spec.url.rstrip("/").removesuffix(".git").lower()
        if key not in seen:
            seen.add(key)
            unique.append(spec)
    return unique


def head_sha(spec: RepoSpec) -> str:
    ref = spec.branch or "HEAD"
    try:
        out = _git("ls-remote", _authed(spec.url), ref)
        return out.split()[0] if out else ""
    except (subprocess.SubprocessError, IndexError) as exc:
        log.warning("ls-remote failed for %s: %s", spec.url, type(exc).__name__)
        return ""


def _tree(root: Path, max_entries: int = 120) -> list[str]:
    entries = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or len(rel.parts) > 3:
            continue
        entries.append(str(rel) + ("/" if path.is_dir() else ""))
        if len(entries) >= max_entries:
            entries.append("...")
            break
    return entries


def _languages(root: Path) -> list[str]:
    counts: Counter[str] = Counter()
    for path in root.rglob("*"):
        if path.is_file() and not any(p in SKIP_DIRS for p in path.relative_to(root).parts):
            if lang := EXT_LANG.get(path.suffix.lower()):
                counts[lang] += path.stat().st_size
    total = sum(counts.values()) or 1
    return [f"{lang} {100 * n / total:.0f}%" for lang, n in counts.most_common(8)]


def _read(path: Path, limit: int) -> str:
    try:
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def digest_repo(spec: RepoSpec) -> RepoDigest | None:
    with tempfile.TemporaryDirectory() as tmp:
        args = ["clone", "--depth", "1", "--quiet"]
        if spec.branch:
            args += ["--branch", spec.branch]
        try:
            _git(*args, _authed(spec.url), tmp)
            head = _git("rev-parse", "HEAD", cwd=tmp).strip()
        except subprocess.SubprocessError as exc:
            log.warning("Could not clone %s: %s", spec.url, type(exc).__name__)
            return None
        root = Path(tmp)
        readme = next((p for p in root.iterdir() if p.name.lower().startswith("readme")), None)
        parts = [
            f"## Repository: {spec.url}",
            f"Languages (by size): {', '.join(_languages(root)) or 'n/a'}",
            "### File tree (depth<=3)",
            "\n".join(_tree(root)),
        ]
        if readme:
            parts += ["### README", _read(readme, 6000)]
        for name in MANIFESTS:
            if (root / name).is_file():
                parts += [f"### {name}", _read(root / name, 2000)]
        return RepoDigest(url=spec.url, head=head, text="\n".join(parts))
