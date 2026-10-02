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
    ResumeUse,
    ResumeVersion,
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
    """SeenJobsRepository port: ``data/state.json``.

    An entry is forgotten only after ``retention_days`` without the posting showing up in any search, so
    postings that stay online for months are never notified twice.
    """

    def __init__(self, path: Path, retention_days: int = 90) -> None:
        self.path = path
        self.retention = timedelta(days=retention_days)
        self.seen: dict[str, dict] = {}
        if path.exists():
            self.seen = json.loads(path.read_text()).get("seen", {})

    def is_seen_key(self, key: str) -> bool:
        entry = self.seen.get(key)
        if entry is not None:
            entry["last_seen"] = _now()
        return entry is not None

    def is_duplicate(self, job: JobPosting) -> bool:
        signature, key = duplicate_signature(job), job_key(job)
        return bool(signature) and any(k != key and v.get("dup") == signature for k, v in self.seen.items())

    def mark(self, job: JobPosting, outcome: str, score: int | None = None) -> None:
        now = _now()
        previous = self.seen.get(job_key(job), {})
        self.seen[job_key(job)] = {
            "first_seen": previous.get("first_seen", now),
            "last_seen": now,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "dup": duplicate_signature(job),
            "outcome": outcome,
            "score": score,
        }

    def commit(self) -> None:
        cutoff = datetime.now(timezone.utc) - self.retention
        self.seen = {
            k: v for k, v in self.seen.items()
            if datetime.fromisoformat(v.get("last_seen") or v["first_seen"]) >= cutoff
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen": self.seen}, indent=1, ensure_ascii=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """ApplicationStore port on the local file system: ``<output_dir>/<date>/<company>-<title>-<id>/`` with
    resume.{md,html,pdf}, a README explaining the changes and ``version.json`` (the catalogue entry used to
    decide reuse). Version ids are paths relative to ``output_dir``."""

    VERSION_FILE = "version.json"

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def save(
        self,
        job: JobPosting,
        match: JobMatch,
        tailored: TailoredResume,
        base_fingerprint: str,
        adapted_from: str | None = None,
    ) -> SavedApplication:
        now = datetime.now(timezone.utc)
        folder = self.output_dir / now.strftime("%Y-%m-%d") / f"{_slug(job.company, 25)}-{_slug(job.title)}-{job_key(job)[:6]}"
        write_outputs(tailored.resume_markdown, folder)
        (folder / "README.md").write_text(self._readme(job, match, tailored, adapted_from))
        version = ResumeVersion(
            id=self._id(folder),
            job_key=job_key(job),
            job_title=job.title,
            company=job.company,
            language=tailored.language,
            highlights=tailored.highlights,
            base_fingerprint=base_fingerprint,
            created_at=now,
            adapted_from=adapted_from,
            used_for=[ResumeUse(job_key=job_key(job), title=job.title, company=job.company, at=now)],
        )
        self._write_version(folder, version)
        return self.locate(version.id)

    def list_versions(self) -> list[ResumeVersion]:
        if not self.output_dir.exists():
            return []
        return [
            ResumeVersion.model_validate_json(p.read_text())
            for p in sorted(self.output_dir.glob(f"*/*/{self.VERSION_FILE}"))
        ]

    def load_markdown(self, version_id: str) -> str:
        return (self._folder(version_id) / "resume.md").read_text()

    def locate(self, version_id: str) -> SavedApplication:
        folder = self._folder(version_id)
        pdf, md = folder / "resume.pdf", folder / "resume.md"
        if not md.exists():
            raise FileNotFoundError(f"No resume stored for version {version_id}")
        return SavedApplication(version_id=version_id, folder=str(folder), attachment=pdf if pdf.exists() else md)

    def record_use(self, version_id: str, job: JobPosting) -> None:
        folder = self._folder(version_id)
        version = ResumeVersion.model_validate_json((folder / self.VERSION_FILE).read_text())
        version.used_for.append(
            ResumeUse(job_key=job_key(job), title=job.title, company=job.company, at=datetime.now(timezone.utc))
        )
        self._write_version(folder, version)

    def _id(self, folder: Path) -> str:
        return folder.relative_to(self.output_dir).as_posix()

    def _folder(self, version_id: str) -> Path:
        folder = (self.output_dir / version_id).resolve()
        if not folder.is_relative_to(self.output_dir.resolve()):
            raise ValueError(f"Invalid resume version id: {version_id}")
        return folder

    def _write_version(self, folder: Path, version: ResumeVersion) -> None:
        (folder / self.VERSION_FILE).write_text(version.model_dump_json(indent=2))

    @staticmethod
    def _readme(job: JobPosting, match: JobMatch, tailored: TailoredResume, adapted_from: str | None) -> str:
        changes = "\n".join(f"- **{c.section}**: {c.change}\n  - _{c.rationale}_" for c in tailored.changes)
        origin = f"- Adaptada a partir de: `{adapted_from}`\n" if adapted_from else ""
        return (
            f"# {job.title} — {job.company}\n\n"
            f"- Fuente: {job.source}\n- Ubicación: {job.location} ({job.remote})\n- URL: {job.url}\n"
            f"- Afinidad: {match.score}/100 ({match.verdict})\n{origin}\n"
            f"## Cambios en la hoja de vida\n\n{tailored.summary_for_candidate}\n\n{changes}\n\n"
            "## Por qué encaja\n\n" + "\n".join(f"- {r}" for r in match.reasons) + "\n\n"
            "## Brechas\n\n" + "\n".join(f"- {g}" for g in match.gaps) + "\n\n"
            f"## Descripción original\n\n{job.description}\n"
        )
