import logging

import httpx
import pytest

from job_agent.scoring.models import PostingEnrichment, ScoreResult


RESULT = ScoreResult(83, 0.9, "typesafe/jev-1.13")


def notifier_for(payload, seen, status=200):
    from job_agent.scoring.telegram import TelegramOfferNotifier

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    return TelegramOfferNotifier("SECRET-TOKEN", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_sends_required_and_optional_offer_details(job):
    seen = []
    notifier_for({"ok": True}, seen).notify(
        job, RESULT, PostingEnrichment("English B2", "USD 2,000 - 3,000")
    )

    [request] = seen
    assert str(request.url) == "https://api.telegram.org/botSECRET-TOKEN/sendMessage"
    body = dict(httpx.QueryParams(request.content.decode()))
    assert body["chat_id"] == "42"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] == "true"
    message = body["text"]
    assert "Backend Engineer &lt;Python&gt;" in message
    assert "Acme &amp; Co" in message
    assert "Bogotá" in message
    assert job.url.replace("&", "&amp;") in message
    assert "83/100" in message
    assert "English B2" in message
    assert "USD 2,000 - 3,000" in message
    assert job.description not in message


def test_omits_unknown_language_and_salary(job):
    seen = []
    notifier_for({"ok": True}, seen).notify(job, RESULT, PostingEnrichment())
    message = dict(httpx.QueryParams(seen[0].content.decode()))["text"]
    assert "Idioma" not in message
    assert "Salario" not in message


def test_long_untrusted_fields_stay_under_telegram_limit(job):
    long_job = job.model_copy(update={"title": "<" * 5000, "url": "https://example.com/?q=" + "&" * 5000})
    seen = []

    notifier_for({"ok": True}, seen).notify(long_job, RESULT, PostingEnrichment())

    message = dict(httpx.QueryParams(seen[0].content.decode()))["text"]
    assert len(message) <= 4096
    assert "<" not in message.replace("<b>", "").replace("</b>", "")


@pytest.mark.parametrize("token,chat_id", [(" ", "42"), ("key", " ")])
def test_requires_credentials(token, chat_id):
    from job_agent.scoring.telegram import TelegramOfferNotifier
    with pytest.raises(ValueError):
        TelegramOfferNotifier(token, chat_id)


@pytest.mark.parametrize("status,payload", [(500, {"ok": False}), (200, {"ok": False})])
def test_rejected_send_raises_without_logging_secrets(job, caplog, status, payload):
    seen = []
    with caplog.at_level(logging.WARNING):
        with pytest.raises(Exception):
            notifier_for(payload, seen, status=status).notify(job, RESULT, PostingEnrichment())

    assert len(seen) == 1
    assert "SECRET-TOKEN" not in caplog.text
    assert job.description not in caplog.text


def test_network_error_propagates(job):
    from job_agent.scoring.telegram import TelegramOfferNotifier

    def fail(request):
        raise httpx.ConnectError("offline", request=request)

    notifier = TelegramOfferNotifier(
        "SECRET-TOKEN", "42", client=httpx.Client(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(httpx.ConnectError):
        notifier.notify(job, RESULT, PostingEnrichment())
