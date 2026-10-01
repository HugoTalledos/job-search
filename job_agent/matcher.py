"""Score each posting against the candidate profile and current resume."""

from __future__ import annotations

import json
import logging

from . import llm
from .models import JobMatch, JobPosting, Profile

log = logging.getLogger(__name__)

SYSTEM = """You evaluate how well a candidate fits a job posting.
Score 0-100: 85+ strong fit (meets nearly all must-haves), 70-84 good fit (meets most must-haves,
gaps are learnable), 50-69 weak, below 50 no.
Judge the CANDIDATE (profile, which includes evidence from their code repositories), not just the resume.
Set `resume_undersells` to true only when the candidate genuinely fits but the resume as written would
likely be filtered out or fail to show the relevant evidence (missing keywords for skills they have,
relevant projects omitted, wrong emphasis). `tailoring_focus` must only contain things that are true
according to the profile or resume. Write reasons, gaps and focus in Spanish."""


def score_job(job: JobPosting, profile: Profile, resume_text: str) -> JobMatch:
    content = [
        # Stable prefix first so it is cached across the postings scored in one run.
        {
            "type": "text",
            "text": f"<profile>\n{profile.model_dump_json(indent=1)}\n</profile>\n<resume>\n{resume_text}\n</resume>",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": "<job>\n" + json.dumps(job.model_dump(), ensure_ascii=False, indent=1) + "\n</job>"},
    ]
    return llm.structured(system=SYSTEM, content=content, schema=JobMatch, effort="low", max_tokens=8000)
