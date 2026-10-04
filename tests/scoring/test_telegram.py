import json
import logging

import httpx
import pytest

from job_agent.scoring.models import PostingEnrichment, ScoreResult, TelegramMessageRef


RESULT = ScoreResult(83, 0.9, "typesafe/jev-1.13")


def notifier_for(payload, seen, status=200):
    from job_agent.scoring.telegram import TelegramOfferNotifier

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    return TelegramOfferNotifier("SECRET-TOKEN", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_sends_required_and_optional_offer_details(job):
    seen = []
    notifier_for({"ok": True, "result": {"message_id": 91}}, seen).notify(
        job, RESULT, PostingEnrichment("English B2", "USD 2,000 - 3,000")
    )

    [request] = seen
    assert str(request.url) == "https://api.telegram.org/botSECRET-TOKEN/sendMessage"
    body = dict(httpx.QueryParams(request.content.decode()))
    assert body["chat_id"] == "42"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] == "true"
    assert json.loads(body["reply_markup"]) == {
        "inline_keyboard": [[{"text": "📄 Ajustar CV", "callback_data": "cv:adjust"}]]}
    message = body["text"]
    assert "Backend Engineer &lt;Python&gt;" in message
    assert "Acme &amp; Co" in message
    assert "Bogotá" in message
    assert f'href="{job.url.replace("&", "&amp;")}"' in message
    assert "83/100" in message
    assert "English B2" in message
    assert "USD 2,000 - 3,000" in message
    assert job.description not in message


def test_omits_unknown_language_and_salary(job):
    seen = []
    notifier_for({"ok": True, "result": {"message_id": 91}}, seen).notify(job, RESULT, PostingEnrichment())
    message = dict(httpx.QueryParams(seen[0].content.decode()))["text"]
    assert "Idioma" not in message
    assert "Salario" not in message


def test_long_title_stays_under_limit_without_changing_destination(job):
    long_url = "https://example.com/apply?ref=" + "a" * 1500
    long_job = job.model_copy(update={"title": "<" * 5000, "url": long_url})
    seen = []

    notifier_for({"ok": True, "result": {"message_id": 91}}, seen).notify(long_job, RESULT, PostingEnrichment())

    message = dict(httpx.QueryParams(seen[0].content.decode()))["text"]
    assert len(message) <= 4096
    assert "&lt;" in message
    assert f'href="{long_url}"' in message


def test_url_that_cannot_fit_rejects_send_instead_of_silently_truncating(job):
    long_job = job.model_copy(update={"url": "https://example.com/?q=" + "a" * 5000})
    seen = []
    with pytest.raises(ValueError, match="limit"):
        notifier_for({"ok": True, "result": {"message_id": 91}}, seen).notify(long_job, RESULT, PostingEnrichment())
    assert seen == []


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


def test_returns_sent_message_reference(job):

    ref = notifier_for({"ok": True, "result": {"message_id": 91}}, []).notify(job, RESULT, PostingEnrichment())
    assert ref == TelegramMessageRef("42", 91)


@pytest.mark.parametrize("payload", [
    {"ok": True}, {"ok": True, "result": None}, {"ok": True, "result": []},
    {"ok": True, "result": {}}, {"ok": True, "result": {"message_id": "91"}},
    {"ok": True, "result": {"message_id": True}}, {"ok": True, "result": {"message_id": 0}},
    {"ok": True, "result": {"message_id": -1}}, [], None,
])
def test_malformed_receipt_raises_without_logging_secrets(job, caplog, payload):
    with pytest.raises(RuntimeError):
        notifier_for(payload, []).notify(job, RESULT, PostingEnrichment())
    assert "SECRET-TOKEN" not in caplog.text
    assert job.description not in caplog.text


def test_malformed_json_raises_without_exposing_response(job):
    from job_agent.scoring.telegram import TelegramOfferNotifier

    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="SECRET-RESPONSE")))
    with pytest.raises(RuntimeError, match="Telegram") as error:
        TelegramOfferNotifier("SECRET-TOKEN", "42", client).notify(job, RESULT, PostingEnrichment())
    assert "SECRET-RESPONSE" not in str(error.value)
