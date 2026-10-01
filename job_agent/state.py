"""Persisted run state: which postings were already processed."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT
from .models import JobPosting

STATE_PATH = ROOT / "data" / "state.json"
HISTORY_PATH = ROOT / "data" / "matches.jsonl"
RETENTION_DAYS = 90


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def job_key(job: JobPosting) -> str:
    """Stable id: source id when present, else canonical url, else company+title."""
    if job.external_id:
        basis = f"{_norm(job.source)}:{job.external_id}"
    elif job.url:
        basis = re.sub(r"[?#].*$", "", job.url.strip().lower()).rstrip("/")
    else:
        basis = f"{_norm(job.company)}|{_norm(job.title)}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


class State:
    def __init__(self, path: Path | None = None):
        self.path = path = path or STATE_PATH
        self.seen: dict[str, dict] = {}
        if path.exists():
            self.seen = json.loads(path.read_text()).get("seen", {})

    def is_seen(self, job: JobPosting) -> bool:
        if job_key(job) in self.seen:
            return True
        # Same role reposted under a new id: match on company + title.
        dup = f"{_norm(job.company)}|{_norm(job.title)}"
        return any(v.get("dup") == dup for v in self.seen.values())

    def seen_urls(self) -> list[str]:
        return [v["url"] for v in self.seen.values() if v.get("url")]

    def mark(self, job: JobPosting, score: int | None) -> None:
        self.seen[job_key(job)] = {
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "dup": f"{_norm(job.company)}|{_norm(job.title)}",
            "score": score,
        }

    def save(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        self.seen = {k: v for k, v in self.seen.items() if datetime.fromisoformat(v["first_seen"]) >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen": self.seen}, indent=1, ensure_ascii=False))


def append_history(record: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
