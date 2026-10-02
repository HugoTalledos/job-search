"""JobMatcher port implemented with Claude."""

from __future__ import annotations

import json

from ...domain.models import JobMatch, JobPosting, Profile
from . import claude_client

SYSTEM = """You evaluate how well a candidate fits a job posting.
Score 0-100: 85+ strong fit (meets nearly all must-haves), 70-84 good fit (meets most must-haves,
gaps are learnable), 50-69 weak, below 50 no.
Judge the CANDIDATE (profile, which includes evidence from their code repositories), not just the resume.
Set `resume_undersells` to true only when the candidate genuinely fits but the resume as written would
likely be filtered out or fail to show the relevant evidence (missing keywords for skills they have,
relevant projects omitted, wrong emphasis). `tailoring_focus` must only contain things that are true
according to the profile or resume. Write reasons, gaps and focus in Spanish.
The posting comes from a job board's page text: some fields may be empty. Copy the job title, hiring
company and location exactly as the text states them into `posting_title`, `posting_company` and
`posting_location` (empty string if absent)."""


class ClaudeJobMatcher:
    def score(self, job: JobPosting, profile: Profile, resume_text: str) -> JobMatch:
        content = [
            # Stable prefix first so it is cached across the postings scored in one run.
            {
                "type": "text",
                "text": f"<profile>\n{profile.model_dump_json(indent=1)}\n</profile>\n<resume>\n{resume_text}\n</resume>",
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": "<job>\n" + json.dumps(job.model_dump(), ensure_ascii=False, indent=1) + "\n</job>"},
        ]
        return claude_client.structured(system=SYSTEM, content=content, schema=JobMatch, effort="low", max_tokens=8000)
