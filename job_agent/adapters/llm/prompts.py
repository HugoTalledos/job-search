"""Prompts and message content for every LLM task. Provider-neutral: each model adapter (Anthropic,
OpenRouter...) receives the same system prompt and content blocks and only handles transport."""

from __future__ import annotations

import json

from ...domain.models import JobMatch, JobPosting, Profile, RepoEvidence, ResumeVersion

# Content blocks use the Anthropic shape ({"type": "text", "text": ..., "cache_control"?}); adapters for
# other providers translate them. "cache_control" marks the end of a prefix that is stable across calls.

PROFILE_SYSTEM = """You are a senior technical recruiter building a candidate profile.
You receive the candidate's resume and digests of their code repositories.
Infer the profile strictly from this evidence. Repositories are evidence of skills actually used;
note in `strengths_missing_from_resume` what the code proves but the resume omits or undersells.
Never invent employers, dates, degrees or certifications. Keyword queries should be short
(2-4 words) and in the language employers in the candidate's market would use."""

MATCH_SYSTEM = """You evaluate how well a candidate fits a job posting.
Score 0-100: 85+ strong fit (meets nearly all must-haves), 70-84 good fit (meets most must-haves,
gaps are learnable), 50-69 weak, below 50 no.
Judge the CANDIDATE (profile, which includes evidence from their code repositories), not just the resume.
Set `resume_undersells` to true only when the candidate genuinely fits but the resume as written would
likely be filtered out or fail to show the relevant evidence (missing keywords for skills they have,
relevant projects omitted, wrong emphasis). `tailoring_focus` must only contain things that are true
according to the profile or resume. Write reasons, gaps and focus in Spanish.
List every explicit skill and prerequisite in `requirements`. Set `priority` to `must` for required
qualifications and `nice` for explicitly desirable qualifications. Set `covered` to true only when
the base resume or candidate profile demonstrates the requirement; cite that concrete evidence in
`evidence` in Spanish. Otherwise set `covered` to false, explain the missing evidence in `evidence`
and include the gap in `gaps`. Never treat an unsupported requirement as a candidate skill or invent
evidence. Do not infer requirements that the posting does not state.
The posting comes from a job board's page text: some fields may be empty. Copy the job title, hiring
company and location exactly as the text states them into `posting_title`, `posting_company` and
`posting_location` (empty string if absent). Also copy the English level the posting requires into
`english_level` and the salary or salary range it states (with currency and period) into `salary_range`;
use an empty string when the posting does not mention them - never guess."""

SELECT_SYSTEM = """You manage a candidate's library of resumes already tailored to past job postings.
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

TAILOR_SYSTEM = """You tailor a candidate's resume to a specific job posting.

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
Set `language` to the resume's language and `highlights` to what this version puts forward.
List each modification in `changes` and summarise them for the candidate (in Spanish).

When a <starting_version> is given, it is a resume already tailored (from the same base resume) for a
similar posting. Start from it and make the smallest changes that make it fit the new posting; list
only those changes, relative to the starting version. The hard rules still apply: anything you add must
be backed by the base resume or the profile."""


def _text(text: str, cache: bool = False) -> dict:
    block = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return block


def _job_json(job: JobPosting) -> str:
    return json.dumps(job.model_dump(), ensure_ascii=False, indent=1)


def profile_content(resume_text: str, evidence: list[RepoEvidence], preferred_locations: list[str]) -> list[dict]:
    return [
        _text(f"<resume>\n{resume_text}\n</resume>"),
        _text("<repositories>\n" + "\n\n".join(e.summary for e in evidence) + "\n</repositories>"),
        _text(f"Build the candidate profile. Preferred locations: {preferred_locations}."),
    ]


def match_content(job: JobPosting, profile: Profile, resume_text: str) -> list[dict]:
    return [
        # Stable prefix first so it is cached across the postings scored in one run.
        _text(f"<profile>\n{profile.model_dump_json(indent=1)}\n</profile>\n<resume>\n{resume_text}\n</resume>", cache=True),
        _text(f"<job>\n{_job_json(job)}\n</job>"),
    ]


def select_content(job: JobPosting, match: JobMatch, candidates: list[ResumeVersion]) -> list[dict]:
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
    return [
        _text(
            f"<new_posting>\n{_job_json(job)}\n</new_posting>\n"
            f"<fit_analysis>\n{match.model_dump_json(indent=1)}\n</fit_analysis>\n"
            "<resume_library>\n" + json.dumps(library, ensure_ascii=False, indent=1) + "\n</resume_library>"
        )
    ]


def tailor_content(
    job: JobPosting, match: JobMatch, profile: Profile, resume_text: str, starting_from: str | None
) -> list[dict]:
    content = [
        _text(f"<base_resume>\n{resume_text}\n</base_resume>\n<profile>\n{profile.model_dump_json(indent=1)}\n</profile>", cache=True),
        _text(f"<job>\n{_job_json(job)}\n</job>\n<fit_analysis>\n{match.model_dump_json(indent=1)}\n</fit_analysis>"),
    ]
    if starting_from:
        content.append(_text(f"<starting_version>\n{starting_from}\n</starting_version>"))
    return content
