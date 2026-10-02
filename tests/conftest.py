import pytest

from job_agent.domain.models import JobMatch, JobPosting, Profile, ResumeChange, Skill, TailoredResume


@pytest.fixture
def job():
    return JobPosting(
        source="linkedin", external_id="123", title="Backend Engineer <Python>", company="Acme & Co",
        location="Bogotá", url="https://www.linkedin.com/jobs/view/123/?trk=x", remote="remote",
        posted_at="2026-10-01", description="Python, FastAPI, AWS",
    )


@pytest.fixture
def profile():
    return Profile(
        full_name="Test", headline="Backend dev", seniority="mid", years_of_experience=4, summary="s",
        target_roles=["Backend Engineer"], search_keywords=["python backend"],
        skills=[Skill(name="Python", level="advanced", evidence="repo x")], domains=["fintech"],
        languages=["Español (nativo)"], locations=["Remote"], notable_projects=["x"],
        strengths_missing_from_resume=["AWS Lambda"],
    )


@pytest.fixture
def match():
    return JobMatch(score=82, verdict="good", reasons=["Python avanzado"], gaps=["Kubernetes"],
                    resume_undersells=True, tailoring_focus=["Destacar AWS"],
                    posting_title="Backend Engineer", posting_company="Acme", posting_location="Bogotá")


@pytest.fixture
def tailored():
    return TailoredResume(
        resume_markdown="# Test\n\n## Resumen\nBackend dev",
        language="es",
        highlights=["Python", "AWS"],
        changes=[ResumeChange(section="Resumen", change="Enfocado a backend", rationale="La oferta pide Python")],
        summary_for_candidate="Se reescribió el resumen.",
    )
