from datetime import datetime, timedelta, timezone

from job_agent.adapters.job_sources.linkedin_text import parse_job_posting

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def test_english_posting():
    text = ("Acme Inc.\nShare\nShow more options\nBackend Engineer (Python)\n"
            "Bogotá, D.C., Colombia · Reposted 2 days ago · Over 100 applicants\n"
            "Promoted by hirer · Responses managed off LinkedIn\nHybrid\nFull-time\nMid-Senior level\n"
            "Easy Apply\nSave\nAbout the job\nWe build payments.\nRequirements: Python.")
    job = parse_job_posting("1", "https://www.linkedin.com/jobs/view/1/", text, NOW)
    assert (job.company, job.title, job.location, job.remote) == (
        "Acme Inc.", "Backend Engineer (Python)", "Bogotá, D.C., Colombia", "hybrid")
    assert datetime.fromisoformat(job.posted_at) == NOW - timedelta(days=2)
    assert job.description == "We build payments.\nRequirements: Python."
    assert (job.source, job.external_id) == ("linkedin", "1")


def test_spanish_posting():
    text = ("Globant\nCompartir\nIngeniero de Datos Sr\nMedellín, Antioquia, Colombia · hace 5 horas · 30 solicitudes\n"
            "En remoto\nJornada completa\nAcerca del empleo\nSpark, Airflow.")
    job = parse_job_posting("2", "u", text, NOW)
    assert (job.company, job.title, job.location, job.remote) == (
        "Globant", "Ingeniero de Datos Sr", "Medellín, Antioquia, Colombia", "remote")
    assert datetime.fromisoformat(job.posted_at) == NOW - timedelta(hours=5)


def test_unknown_layout_keeps_text_and_leaves_fields_empty():
    job = parse_job_posting("3", "u", "Something unexpected\nwithout the usual header", NOW)
    assert (job.title, job.company, job.location, job.posted_at, job.remote) == ("", "", "", "", "unknown")
    assert "Something unexpected" in job.description
