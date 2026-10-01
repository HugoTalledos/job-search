"""End-to-end run: profile -> search -> match -> tailor -> notify."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, Config
from .matcher import score_job
from .models import JobMatch, JobPosting, TailoredResume
from .notifier import Notifier, format_message
from .profile import ensure_profile
from .resume import read_resume, write_outputs
from .sources import search_jobs
from .state import State, append_history, job_key
from .tailor import tailor_resume

log = logging.getLogger(__name__)

OUTPUT_DIR = ROOT / "output"


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "job"


def _repo_link(path: Path) -> str | None:
    server, repo, ref = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_REF_NAME"))
    if not (server and repo and ref):
        return None
    return f"{server}/{repo}/blob/{ref}/{path.relative_to(ROOT).as_posix()}"


def _save_application(job: JobPosting, match: JobMatch, tailored: TailoredResume) -> dict[str, Path]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    folder = OUTPUT_DIR / day / f"{_slug(job.company, 25)}-{_slug(job.title)}-{job_key(job)[:6]}"
    paths = write_outputs(tailored.resume_markdown, folder)
    changes = "\n".join(f"- **{c.section}**: {c.change}\n  - _{c.rationale}_" for c in tailored.changes)
    (folder / "README.md").write_text(
        f"# {job.title} — {job.company}\n\n"
        f"- Fuente: {job.source}\n- Ubicación: {job.location} ({job.remote})\n- URL: {job.url}\n"
        f"- Afinidad: {match.score}/100 ({match.verdict})\n\n"
        f"## Cambios en la hoja de vida\n\n{tailored.summary_for_candidate}\n\n{changes}\n\n"
        f"## Por qué encaja\n\n" + "\n".join(f"- {r}" for r in match.reasons) + "\n\n"
        f"## Brechas\n\n" + "\n".join(f"- {g}" for g in match.gaps) + "\n\n"
        f"## Descripción original\n\n{job.description}\n"
    )
    return paths


def run(cfg: Config, dry_run: bool = False) -> int:
    """Execute one search cycle. Returns the number of notifications sent."""
    profile = ensure_profile(cfg)
    resume_text = read_resume(cfg.resume_file)
    state = State()
    notifier = Notifier()

    jobs = asyncio.run(search_jobs(profile, cfg.search, state.seen_urls()))
    excluded = {c.lower() for c in cfg.search.exclude_companies}
    fresh, keys = [], set()
    for job in jobs:
        key = job_key(job)
        if key in keys or state.is_seen(job) or job.company.lower() in excluded:
            continue
        keys.add(key)
        fresh.append(job)
    log.info("%d postings found, %d new", len(jobs), len(fresh))

    sent = 0
    for job in fresh:
        try:
            match = score_job(job, profile, resume_text)
        except Exception:
            log.exception("Scoring failed for %s @ %s", job.title, job.company)
            continue
        log.info("%3d  %s @ %s", match.score, job.title, job.company)

        tailored, paths = None, {}
        if match.score >= cfg.matching.min_score_to_notify:
            if match.resume_undersells and match.score >= cfg.matching.min_score_to_tailor:
                try:
                    tailored = tailor_resume(job, match, profile, resume_text)
                    paths = _save_application(job, match, tailored)
                except Exception:
                    log.exception("Tailoring failed for %s @ %s", job.title, job.company)
            attachment = paths.get("pdf") or paths.get("md")
            link = _repo_link(paths["md"]) if paths else None
            text = format_message(job, match, tailored, link)
            if dry_run:
                log.info("[dry-run]\n%s", text)
            else:
                notifier.send(text, attachment)
            sent += 1

        state.mark(job, match.score)
        append_history(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "key": job_key(job),
                "title": job.title,
                "company": job.company,
                "url": job.url,
                "source": job.source,
                "score": match.score,
                "verdict": match.verdict,
                "tailored": bool(tailored),
                "resume_dir": str(paths["md"].parent.relative_to(ROOT)) if paths else None,
            }
        )
        state.save()  # save after each posting so a crash mid-run does not re-notify

    state.save()
    log.info("Run complete: %d notifications", sent)
    return sent
