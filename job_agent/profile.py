"""Infer and persist the candidate profile from the resume and repositories."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import llm
from .config import ROOT, Config
from .models import Profile
from .repos import digest_repo, head_sha, resolve_repos
from .resume import read_resume

log = logging.getLogger(__name__)

PROFILE_PATH = ROOT / "data" / "profile.json"

SYSTEM = """You are a senior technical recruiter building a candidate profile.
You receive the candidate's resume and digests of their code repositories.
Infer the profile strictly from this evidence. Repositories are evidence of skills actually used;
note in `strengths_missing_from_resume` what the code proves but the resume omits or undersells.
Never invent employers, dates, degrees or certifications. Keyword queries should be short
(2-4 words) and in the language employers in the candidate's market would use."""


def _fingerprint(resume_text: str, heads: list[str]) -> str:
    h = hashlib.sha256(resume_text.encode())
    for head in sorted(heads):
        h.update(head.encode())
    return h.hexdigest()


def load_profile() -> Profile | None:
    if not PROFILE_PATH.exists():
        return None
    return Profile.model_validate(json.loads(PROFILE_PATH.read_text())["profile"])


def ensure_profile(cfg: Config, force: bool = False) -> Profile:
    """Return the persisted profile, rebuilding it when the resume or repos changed or it is stale."""
    resume_text = read_resume(cfg.resume_file)
    specs = resolve_repos(cfg)
    fingerprint = _fingerprint(resume_text, [s.url + head_sha(s) for s in specs])

    if PROFILE_PATH.exists() and not force:
        stored = json.loads(PROFILE_PATH.read_text())
        built = datetime.fromisoformat(stored["built_at"])
        fresh = datetime.now(timezone.utc) - built < timedelta(days=cfg.profile_refresh_days)
        if stored.get("fingerprint") == fingerprint and fresh:
            return Profile.model_validate(stored["profile"])

    log.info("Building profile from resume + %d repositories", len(specs))
    digests = [d for s in specs if (d := digest_repo(s))]
    content = [
        {"type": "text", "text": f"<resume>\n{resume_text}\n</resume>"},
        {"type": "text", "text": "<repositories>\n" + "\n\n".join(d.text for d in digests) + "\n</repositories>"},
        {"type": "text", "text": f"Build the candidate profile. Preferred locations from config: {cfg.search.locations}."},
    ]
    profile = llm.structured(system=SYSTEM, content=content, schema=Profile, effort="high")
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(
            {
                "built_at": datetime.now(timezone.utc).isoformat(),
                "fingerprint": fingerprint,
                "repositories": [{"url": d.url, "head": d.head} for d in digests],
                "profile": profile.model_dump(),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return profile
