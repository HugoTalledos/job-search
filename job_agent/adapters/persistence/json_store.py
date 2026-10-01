"""File-based persistence adapters: profile, seen postings, match history, tailored applications."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ...domain.models import (
    JobMatch,
    JobPosting,
    MatchRecord,
    SavedApplication,
    StoredProfile,
    TailoredResume,
)
from ...domain.policies import duplicate_signature, job_key
from ..resume.markdown_renderer import write_outputs


class JsonProfileStore:
    """ProfileStore port: ``data/profile.json``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> StoredProfile | None:
        if not self.path.exists():
            return None
        return StoredProfile.model_validate_json(self.path.read_text())

    def save(self, stored: StoredProfile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(stored.model_dump_json(indent=2))


class JsonSeenJobsRepository:
    """SeenJobsRepository port: ``data/state.json`` with a retention window."""

    def __init__(self, path: Path, retention_days: int = 90) -> None:
        self.path = path
        self.retention = timedelta(days=retention_days)
        self.seen: dict[str, dict] = {}
        if path.exists():
            self.seen = json.loads(path.read_text()).get("seen", {})

    def is_seen(self, job: JobPosting) -> bool:
        if job_key(job) in self.seen:
            return True
        dup = duplicate_signature(job)
        return any(v.get("dup") == dup for v in self.seen.values())

    def seen_urls(self) -> list[str]:
        return [v["url"] for v in self.seen.values() if v.get("url")]

    def mark(self, job: JobPosting, score: int | None) -> None:
        self.seen[job_key(job)] = {
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "dup": duplicate_signature(job),
            "score": score,
        }

    def commit(self) -> None:
        cutoff = datetime.now(timezone.utc) - self.retention
        self.seen = {k: v for k, v in self.seen.items() if datetime.fromisoformat(v["first_seen"]) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen": self.seen}, indent=1, ensure_ascii=False))


class JsonlMatchHistory:
    """MatchHistory port: append-only ``data/matches.jsonl``."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: MatchRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(record.model_dump_json() + "\n")


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "job"


class FileSystemApplicationStore:
    """ApplicationStore port: ``output/<date>/<company>-<title>-<id>/`` with resume.{md,html,pdf} + README."""

    def __init__(self, output_dir: Path, project_root: Path, web_url_base: str | None = None) -> None:
        self.output_dir = output_dir
        self.project_root = project_root
        self.web_url_base = web_url_base  # e.g. https://github.com/<owner>/<repo>/blob/<branch>

    def save(self, job: JobPosting, match: JobMatch, tailored: TailoredResume) -> SavedApplication:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        folder = self.output_dir / day / f"{_slug(job.company, 25)}-{_slug(job.title)}-{job_key(job)[:6]}"
        paths = write_outputs(tailored.resume_markdown, folder)
        (folder / "README.md").write_text(self._readme(job, match, tailored))
        rel = paths["md"].relative_to(self.project_root).as_posix()
        return SavedApplication(
            folder=folder.relative_to(self.project_root).as_posix(),
            attachment=paths.get("pdf") or paths["md"],
            link=f"{self.web_url_base}/{rel}" if self.web_url_base else None,
        )

    @staticmethod
    def _readme(job: JobPosting, match: JobMatch, tailored: TailoredResume) -> str:
        changes = "\n".join(f"- **{c.section}**: {c.change}\n  - _{c.rationale}_" for c in tailored.changes)
        return (
            f"# {job.title} — {job.company}\n\n"
            f"- Fuente: {job.source}\n- Ubicación: {job.location} ({job.remote})\n- URL: {job.url}\n"
            f"- Afinidad: {match.score}/100 ({match.verdict})\n\n"
            f"## Cambios en la hoja de vida\n\n{tailored.summary_for_candidate}\n\n{changes}\n\n"
            "## Por qué encaja\n\n" + "\n".join(f"- {r}" for r in match.reasons) + "\n\n"
            "## Brechas\n\n" + "\n".join(f"- {g}" for g in match.gaps) + "\n\n"
            f"## Descripción original\n\n{job.description}\n"
        )
