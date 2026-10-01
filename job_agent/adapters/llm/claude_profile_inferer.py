"""ProfileInferer port implemented with Claude."""

from __future__ import annotations

from ...domain.models import Profile, RepoEvidence
from . import claude_client

SYSTEM = """You are a senior technical recruiter building a candidate profile.
You receive the candidate's resume and digests of their code repositories.
Infer the profile strictly from this evidence. Repositories are evidence of skills actually used;
note in `strengths_missing_from_resume` what the code proves but the resume omits or undersells.
Never invent employers, dates, degrees or certifications. Keyword queries should be short
(2-4 words) and in the language employers in the candidate's market would use."""


class ClaudeProfileInferer:
    def infer(self, resume_text: str, evidence: list[RepoEvidence], preferred_locations: list[str]) -> Profile:
        content = [
            {"type": "text", "text": f"<resume>\n{resume_text}\n</resume>"},
            {"type": "text", "text": "<repositories>\n" + "\n\n".join(e.summary for e in evidence) + "\n</repositories>"},
            {"type": "text", "text": f"Build the candidate profile. Preferred locations: {preferred_locations}."},
        ]
        return claude_client.structured(system=SYSTEM, content=content, schema=Profile, effort="high")
