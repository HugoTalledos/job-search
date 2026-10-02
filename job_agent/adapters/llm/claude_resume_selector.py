"""ResumeSelector port implemented with Claude."""

from __future__ import annotations

import json

from ...domain.models import JobMatch, JobPosting, ResumeVersion, ReuseDecision
from . import claude_client

SYSTEM = """You manage a candidate's library of resumes already tailored to past job postings.
For a new posting, decide whether one of them can be sent as is, adapted, or whether a new one is needed.

- reuse: the version shows every key (must-have) requirement of the new posting that the candidate
  meets according to the fit analysis, its emphasis suits the role, and it is written in the posting's
  language. Different company names do not matter: resumes do not mention the target company.
- adapt: a version is close (same kind of role and language, or most requirements covered) but needs
  small changes - a few keywords, reordering, a project to surface.
- create: no version is close enough (different role family, different language with substantial
  differences, or most key requirements missing).
Prefer reuse only when you are confident; when in doubt between reuse and adapt, choose adapt.
Use the exact `id` of the version. Write the rationale in Spanish."""


class ClaudeResumeSelector:
    def choose(self, job: JobPosting, match: JobMatch, candidates: list[ResumeVersion]) -> ReuseDecision:
        library = [
            {
                "id": v.id,
                "tailored_for": f"{v.job_title} @ {v.company}",
                "language": v.language,
                "highlights": v.highlights,
                "created_at": v.created_at.date().isoformat(),
            }
            for v in candidates
        ]
        content = (
            "<new_posting>\n" + json.dumps(job.model_dump(), ensure_ascii=False, indent=1) + "\n</new_posting>\n"
            "<fit_analysis>\n" + match.model_dump_json(indent=1) + "\n</fit_analysis>\n"
            "<resume_library>\n" + json.dumps(library, ensure_ascii=False, indent=1) + "\n</resume_library>"
        )
        return claude_client.structured(system=SYSTEM, content=content, schema=ReuseDecision, effort="low", max_tokens=8000)
