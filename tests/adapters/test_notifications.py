from datetime import datetime, timezone

from job_agent.adapters.notifications import TelegramNotifier, format_message
from job_agent.adapters.notifications.telegram_notifier import TELEGRAM_LIMIT
from job_agent.domain.models import JobAlert, ResumeVersion, SavedApplication

LINK = "https://github.com/o/r/blob/main/output/x/resume.md"


def _version():
    return ResumeVersion(
        id="output/2026-09-30/globex-backend-abc", job_key="k", job_title="Backend Dev", company="Globex",
        language="es", highlights=["Python"], base_fingerprint="f",
        created_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


def test_created_resume_message_is_short_and_escapes_html(job, match, tailored):
    app = SavedApplication(version_id="v", folder="v", link=LINK)
    text = format_message(JobAlert(job=job, match=match, tailored=tailored, application=app, resume_origin="created"))
    assert "&lt;Python&gt;" in text and "Acme &amp; Co" in text
    assert "82/100" in text and "Inglés: B2" in text and "Salario: USD 3.000 - 4.000 / mes" in text
    assert "reescrita para esta oferta" in text and LINK in text
    # Only the summary and the resume status: no fit reasons, gaps or list of changes.
    assert "Python avanzado" not in text and "Kubernetes" not in text
    assert "Se reescribió el resumen." not in text and "Enfocado a backend" not in text


def test_english_and_salary_are_omitted_when_the_posting_does_not_state_them(job, match):
    silent = match.model_copy(update={"english_level": "", "salary_range": " "})
    text = format_message(JobAlert(job=job, match=silent))
    assert "Inglés" not in text and "Salario" not in text


def test_reused_resume_message(job, match):
    app = SavedApplication(version_id="v", folder="v", link=LINK)
    alert = JobAlert(job=job, match=match, application=app, resume_origin="reused",
                     source_version=_version(), reuse_rationale="Cubre Python y AWS")
    text = format_message(alert)
    assert "reutilizada (versión creada para Backend Dev — Globex)" in text and LINK in text
    assert "Cubre Python y AWS" not in text


def test_adapted_resume_message(job, match, tailored):
    alert = JobAlert(job=job, match=match, tailored=tailored, resume_origin="adapted", source_version=_version(),
                     application=SavedApplication(version_id="v", folder="v"))
    text = format_message(alert)
    assert "reescrita a partir de una existente (versión creada para Backend Dev — Globex)" in text
    assert "Enfocado a backend" not in text


def test_message_shows_local_folder_without_link(job, match, tailored):
    app = SavedApplication(version_id="v", folder="/Users/hugo/dev/job-search/output/2026-10-03/acme-x")
    text = format_message(JobAlert(job=job, match=match, tailored=tailored, application=app, resume_origin="created"))
    assert "📁 <code>/Users/hugo/dev/job-search/output/2026-10-03/acme-x</code>" in text


def test_failed_and_unchanged_resume_messages(job, match):
    assert "no se pudo generar" in format_message(JobAlert(job=job, match=match, resume_failed=True))
    assert "sin cambios" in format_message(JobAlert(job=job, match=match))


def test_message_is_truncated_to_telegram_limit(job, match):
    long = JobAlert(job=job.model_copy(update={"title": "x" * 5000}), match=match)
    assert len(format_message(long)) <= TELEGRAM_LIMIT


def test_telegram_sends_message_and_document(monkeypatch, tmp_path, job, match, tailored):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF")
    calls = []

    class Resp:
        status_code = 200
        text = ""

    def fake_post(url, data=None, files=None, timeout=None):
        calls.append((url.rsplit("/", 1)[1], data, files and files["document"][0]))
        return Resp()

    monkeypatch.setattr("job_agent.adapters.notifications.telegram_notifier.httpx.post", fake_post)
    app = SavedApplication(version_id="v", folder="v", attachment=pdf)
    TelegramNotifier("TOKEN", "42").notify(JobAlert(job=job, match=match, tailored=tailored, application=app))
    assert [c[0] for c in calls] == ["sendMessage", "sendDocument"]
    assert calls[0][1]["chat_id"] == "42" and calls[1][2] == "resume.pdf"
