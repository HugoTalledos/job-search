"""Create a version of the resume tailored to one posting - truthfully."""

from __future__ import annotations

import json

from . import llm
from .models import JobMatch, JobPosting, Profile, TailoredResume

SYSTEM = """You tailor a candidate's resume to a specific job posting.

Hard rules:
- Use ONLY facts present in the base resume or in the candidate profile (which includes evidence from
  their code repositories). Never invent employers, titles, dates, degrees, certifications, metrics or
  technologies. If a requirement is not backed by evidence, leave it out.
- Keep every employer, role and date from the base resume.
- You may: rewrite the summary for this role, reorder sections and bullets, rephrase bullets with the
  posting's vocabulary when it describes the same thing, surface relevant skills, and add a "Projects"
  entry for repositories that demonstrate required skills.
- Write the resume in the same language as the job posting. Keep it to about one or two pages.
- Output clean Markdown: `# Name`, a contact line, then `## Section` headings and bullet lists.
List each modification in `changes` and summarise them for the candidate (in Spanish)."""


def tailor_resume(job: JobPosting, match: JobMatch, profile: Profile, resume_text: str) -> TailoredResume:
    content = [
        {
            "type": "text",
            "text": f"<base_resume>\n{resume_text}\n</base_resume>\n<profile>\n{profile.model_dump_json(indent=1)}\n</profile>",
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": "<job>\n" + json.dumps(job.model_dump(), ensure_ascii=False, indent=1) + "\n</job>\n"
            "<fit_analysis>\n" + match.model_dump_json(indent=1) + "\n</fit_analysis>",
        },
    ]
    return llm.structured(system=SYSTEM, content=content, schema=TailoredResume, effort="high", max_tokens=16000)
