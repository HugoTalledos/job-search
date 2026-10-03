import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_agent.entrypoints.telegram import ALREADY_BUILDING, BUILDING, HELP, RESENDING_PENDING, add_telegram_webhook
from job_agent.scoring.run import ResendReport

SECRET = {"X-Telegram-Bot-Api-Secret-Token": "hook-secret"}


class Messenger:
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(text)


class Builder:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def execute(self):
        self.calls += 1
        if self.error:
            raise self.error


def _client(builder=None, messenger=None):
    app = FastAPI()
    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=messenger or Messenger(),
                         build_profile=builder or Builder(), resend_pending=Builder())
    return TestClient(app)


def test_resend_pending_replies_immediately_and_runs_in_background():
    started, release = threading.Event(), threading.Event()

    class Resender:
        calls = 0

        def execute(self):
            self.calls += 1
            started.set()
            release.wait(timeout=3)
            return ResendReport(pending=1, notified=1)

    resender, messenger = Resender(), Messenger()
    app = FastAPI()
    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=messenger,
                         build_profile=Builder(), resend_pending=resender)
    client = TestClient(app)

    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.post, "/webhooks/telegram", headers=SECRET,
                               json=_update("/resend_pending", update_id=10))
        assert started.wait(timeout=2)
        assert messenger.sent[0] == "Estoy buscando propuestas que hayan quedado pendientes de notificar"
        assert messenger.sent[0] == RESENDING_PENDING
        release.set()
        assert response.result(timeout=3).status_code == 200

    assert resender.calls == 1
    assert messenger.sent[1] == "Reenvío terminado: 1 notificadas, 0 fallidas."


def _update(text, chat_id=42, update_id=1):
    return {"update_id": update_id, "message": {"message_id": 9, "chat": {"id": chat_id}, "text": text}}


@pytest.mark.parametrize("text", ["/build-profile", "/build_profile", "/Build_Profile@job_bot", " /build-profile ya"])
def test_build_profile_replies_at_once_and_builds_in_background(text):
    builder, messenger = Builder(), Messenger()

    response = _client(builder, messenger).post("/webhooks/telegram", headers=SECRET, json=_update(text))

    assert response.status_code == 200
    assert messenger.sent == [BUILDING]
    assert builder.calls == 1


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}])
def test_wrong_secret_is_unauthorized_without_work(headers):
    builder, messenger = Builder(), Messenger()

    response = _client(builder, messenger).post("/webhooks/telegram", headers=headers, json=_update("/build-profile"))

    assert response.status_code == 401
    assert builder.calls == 0 and messenger.sent == []


def test_messages_from_other_chats_are_ignored():
    builder, messenger = Builder(), Messenger()

    response = _client(builder, messenger).post(
        "/webhooks/telegram", headers=SECRET, json=_update("/build-profile", chat_id=7)
    )

    assert response.status_code == 200
    assert builder.calls == 0 and messenger.sent == []


def test_unknown_text_gets_the_command_list():
    builder, messenger = Builder(), Messenger()

    _client(builder, messenger).post("/webhooks/telegram", headers=SECRET, json=_update("hola"))

    assert messenger.sent == [HELP]
    assert builder.calls == 0


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"update_id": 3}', b'{"message": {"chat": {"id": 42}}}'])
def test_unusable_updates_are_acknowledged_without_work(body):
    builder, messenger = Builder(), Messenger()

    response = _client(builder, messenger).post("/webhooks/telegram", headers=SECRET, content=body)

    assert response.status_code == 200
    assert builder.calls == 0 and messenger.sent == []


def test_resent_update_is_handled_once():
    builder, messenger = Builder(), Messenger()
    client = _client(builder, messenger)

    client.post("/webhooks/telegram", headers=SECRET, json=_update("/build-profile", update_id=5))
    client.post("/webhooks/telegram", headers=SECRET, json=_update("/build-profile", update_id=5))

    assert builder.calls == 1 and messenger.sent == [BUILDING]


def test_build_failure_is_logged_without_details_and_releases_the_command(caplog):
    builder, messenger = Builder(error=RuntimeError("SECRET-RESUME-TEXT")), Messenger()
    client = _client(builder, messenger)

    with caplog.at_level(logging.ERROR):
        first = client.post("/webhooks/telegram", headers=SECRET, json=_update("/build-profile", update_id=1))
        client.post("/webhooks/telegram", headers=SECRET, json=_update("/build-profile", update_id=2))

    assert first.status_code == 200
    assert "SECRET-RESUME-TEXT" not in caplog.text and "RuntimeError" in caplog.text
    assert builder.calls == 2 and messenger.sent == [BUILDING, BUILDING]


def test_second_request_while_building_does_not_start_another_build():
    started, release = threading.Event(), threading.Event()

    class BlockingBuilder:
        calls = 0

        def execute(self):
            self.calls += 1
            started.set()
            release.wait(timeout=3)

    builder, messenger = BlockingBuilder(), Messenger()
    client = _client(builder, messenger)

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/webhooks/telegram", headers=SECRET, json=_update("/build-profile", update_id=1))
        assert started.wait(timeout=2)
        try:
            second = client.post("/webhooks/telegram", headers=SECRET, json=_update("/build-profile", update_id=2))
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 200

    assert second.status_code == 200
    assert builder.calls == 1
    assert messenger.sent == [BUILDING, ALREADY_BUILDING]


def test_blank_secret_is_rejected_at_startup():
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        add_telegram_webhook(FastAPI(), secret=" ", chat_id="42", messenger=Messenger(),
                             build_profile=Builder(), resend_pending=Builder())


def test_secret_telegram_would_reject_is_rejected_at_startup():
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        add_telegram_webhook(FastAPI(), secret="has spaces!", chat_id="42", messenger=Messenger(),
                             build_profile=Builder(), resend_pending=Builder())
