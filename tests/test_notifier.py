from job_agent.notifier import TELEGRAM_LIMIT, format_message


def test_message_escapes_html_and_mentions_changes(job, match, tailored):
    text = format_message(job, match, tailored, "https://github.com/o/r/blob/main/output/x/resume.md")
    assert "&lt;Python&gt;" in text and "Acme &amp; Co" in text
    assert "82/100" in text and "Se reescribió el resumen." in text
    assert "Enfocado a backend" in text


def test_message_without_tailoring_and_truncation(job, match):
    text = format_message(job, match.model_copy(update={"reasons": ["x" * 5000]}), None, None)
    assert len(text) <= TELEGRAM_LIMIT
    assert "no se modificó" in format_message(job, match, None, None)
