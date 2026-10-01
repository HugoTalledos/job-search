from pathlib import Path

from job_agent.adapters.notifications import TelegramNotifier, format_message
from job_agent.adapters.notifications.telegram_notifier import TELEGRAM_LIMIT
from job_agent.domain.models import JobAlert, SavedApplication


def test_message_escapes_html_and_mentions_changes(job, match, tailored):
    app = SavedApplication(folder="o", link="https://github.com/o/r/blob/main/o/resume.md")
    text = format_message(JobAlert(job=job, match=match, tailored=tailored, application=app))
    assert "&lt;Python&gt;" in text and "Acme &amp; Co" in text
    assert "82/100" in text and "Se reescribió el resumen." in text and "Enfocado a backend" in text
    assert app.link in text


def test_message_without_tailoring_and_truncation(job, match):
    long = JobAlert(job=job, match=match.model_copy(update={"reasons": ["x" * 5000]}))
    assert len(format_message(long)) <= TELEGRAM_LIMIT
    assert "no se modificó" in format_message(JobAlert(job=job, match=match))


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
    alert = JobAlert(job=job, match=match, tailored=tailored, application=SavedApplication(folder="o", attachment=pdf))
    TelegramNotifier("TOKEN", "42").notify(alert)
    assert [c[0] for c in calls] == ["sendMessage", "sendDocument"]
    assert calls[0][1]["chat_id"] == "42" and calls[1][2] == "resume.pdf"
