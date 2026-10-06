import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from job_agent.entrypoints.telegram import (
    ALREADY_BUILDING, BUILDING, CALLBACK_FAILED, CALLBACK_INVALID, CV_ACCEPTED, CV_BUTTON_ACCEPTED, CV_BUTTON_IN_PROGRESS,
    CV_BUTTON_NOT_STARTED, CV_FAILED, CV_IN_PROGRESS, CV_NEEDS_REPLY, CV_NOT_STARTED, CV_UNKNOWN_OFFER, HELP,
    RESENDING_PENDING, SECRET_HEADER, add_telegram_webhook,
)
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


class NoCv:
    """CV ports for tests of the other commands: any use is a failure."""

    def prepare(self, posting_id, now):
        raise AssertionError("unexpected CV request")

    def execute(self, prepared, chat_id, reply_to_message_id):
        raise AssertionError("unexpected CV generation")

    def resolve(self, chat_id, message_id):
        raise AssertionError("unexpected offer lookup")

    def resolve_unique_url(self, url):
        raise AssertionError("unexpected offer lookup")


class NoPreferences:
    """Preference ports for tests of the other commands: only the post-build plan rebuild is expected."""

    def __init__(self):
        self.rebuilds = 0

    def rebuild_plan(self):
        from job_agent.application.preference_models import RebuildResult

        self.rebuilds += 1
        return RebuildResult(status="no_preferences")

    def __getattr__(self, name):
        raise AssertionError(f"unexpected preferences call: {name}")


CV_PORTS = {"cv_generator": NoCv(), "offer_messages": NoCv(),
            "preferences": NoPreferences(), "preferences_chat": NoPreferences()}


def _client(builder=None, messenger=None):
    app = FastAPI()
    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=messenger or Messenger(),
                         build_profile=builder or Builder(), resend_pending=Builder(), **CV_PORTS)
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
                         build_profile=Builder(), resend_pending=resender, **CV_PORTS)
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
                             build_profile=Builder(), resend_pending=Builder(), **CV_PORTS)


def test_secret_telegram_would_reject_is_rejected_at_startup():
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        add_telegram_webhook(FastAPI(), secret="has spaces!", chat_id="42", messenger=Messenger(),
                             build_profile=Builder(), resend_pending=Builder(), **CV_PORTS)


# --- /ajustar_cv: a reply to a bot offer message generates a tailored CV in the background ---

BOT_ID = 777
OFFER_MESSAGE = {"message_id": 91, "from": {"id": BOT_ID, "is_bot": True}, "chat": {"id": 42, "type": "private"},
                 "text": "Backend Engineer — Ver publicación"}


def _cv_update(text="/ajustar_cv", *, update_id=50, chat=None, sender=42, replied=OFFER_MESSAGE):
    message = {"message_id": 200 + update_id, "chat": chat or {"id": 42, "type": "private"},
               "from": {"id": sender, "is_bot": False}, "text": text}
    if replied is not None:
        message["reply_to_message"] = replied
    return {"update_id": update_id, "message": message}


def _legacy_offer(*entities):
    return {**OFFER_MESSAGE, "message_id": 80, "entities": list(entities)}


def _link(url):
    return {"type": "text_link", "offset": 0, "length": 5, "url": url}


REPLY_COMMAND = _cv_update()
GROUP_REPLY = _cv_update(chat={"id": 42, "type": "group"})


class OfferMessages:
    def __init__(self):
        self.messages = {("42", 91): "offer"}
        self.urls = {"https://jobs.example/offer": "offer"}
        self.url_lookups = []

    def resolve(self, chat_id, message_id):
        return self.messages.get((chat_id, message_id))

    def resolve_unique_url(self, url):
        self.url_lookups.append(url)
        return self.urls.get(url)


@pytest.fixture
def cv(job, profile, match, tailored):
    from types import SimpleNamespace

    from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
    from job_agent.adapters.persistence.firestore_cv_tracking import FirestoreCvTrackingStore
    from job_agent.application import GenerateTailoredCv
    from tests.adapters.test_firebase_cv_artifacts import Client as StorageClient
    from tests.adapters.test_firestore_cv_tracking import Client as FirestoreClient

    events, sent = [], []
    state = SimpleNamespace(profile=profile, fail=None, pdf_calls=[])

    def step(name, result):
        def run(*args):
            events.append(name)
            if state.fail == name:
                raise RuntimeError("SECRET-CV-TEXT from " + name)
            return result(*args)
        return run

    def send_pdf(chat_id, reply_to, pdf, posting_id):
        state.pdf_calls.append((chat_id, reply_to, pdf, posting_id))
        return 300 + len(state.pdf_calls)

    use_case = GenerateTailoredCv(
        resume=SimpleNamespace(read=lambda: "Base CV: Python"),
        profile_reader=SimpleNamespace(load=lambda: state.profile),
        posting_reader=SimpleNamespace(load=lambda posting_id: job),
        matcher=SimpleNamespace(score=step("match", lambda *a: match)),
        tailor=SimpleNamespace(tailor=step("tailor", lambda *a: tailored)),
        renderer=SimpleNamespace(render=step("render", lambda markdown: b"%PDF-cv")),
        tracking=FirestoreCvTrackingStore(FirestoreClient()),
        artifacts=FirebaseCvArtifactStore("private-bucket", StorageClient()),
        delivery=SimpleNamespace(send_summary=step("send_summary", lambda *a: 300),
                                 send_pdf=step("send_pdf", send_pdf)),
    )

    class RecordingMessenger:
        def send_text(self, text):
            events.append("ack" if text == CV_ACCEPTED else "reply")
            sent.append(text)

    answers = []

    class ButtonChat(NoPreferences):
        def answer(self, callback_id, text):
            events.append("answer")
            answers.append((callback_id, text))

    offers = OfferMessages()
    app = FastAPI()
    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=RecordingMessenger(),
                         build_profile=Builder(), resend_pending=Builder(), cv_generator=use_case,
                         offer_messages=offers, bot_id=BOT_ID,
                         preferences=NoPreferences(), preferences_chat=ButtonChat())
    return SimpleNamespace(http=TestClient(app), events=events, sent=sent, state=state, offers=offers,
                           use_case=use_case, answers=answers)


@pytest.fixture
def http(cv):
    return cv.http


@pytest.fixture
def events(cv):
    return cv.events


def test_valid_reply_acknowledges_before_generation(http, events):
    response = http.post("/webhooks/telegram", headers=SECRET, json=REPLY_COMMAND)
    assert response.status_code == 200
    assert events.index("ack") < events.index("match") < events.index("send_pdf")


def test_accepted_request_gets_the_exact_acknowledgment_and_pdf_replies_to_the_offer(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update("/ajustar_cv@job_bot"))

    assert cv.sent == ["Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar."]
    assert cv.state.pdf_calls == [("42", 91, b"%PDF-cv", "offer")]
    assert cv.offers.url_lookups == []


@pytest.mark.parametrize("chat", [{"id": 42, "type": "group"}, {"id": -1001, "type": "supergroup"}, {"id": 42}])
def test_group_reply_does_not_generate(cv, chat):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(chat=chat))
    assert "match" not in cv.events and cv.sent == []


def test_group_reply_fixture_does_not_generate(http, events):
    http.post("/webhooks/telegram", headers=SECRET, json=GROUP_REPLY)
    assert "match" not in events


def test_unique_legacy_url_from_text_link_identifies_the_offer(cv):
    legacy = _legacy_offer(_link("https://jobs.example/offer"), _link("https://jobs.example/offer"))

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(replied=legacy))

    assert cv.offers.url_lookups == ["https://jobs.example/offer"]
    assert cv.sent == [CV_ACCEPTED]
    assert cv.state.pdf_calls == [("42", 80, b"%PDF-cv", "offer")]


@pytest.mark.parametrize("entities, lookups", [
    ([_link("https://jobs.example/offer"), _link("https://jobs.example/other")], []),
    ([_link("https://jobs.example/unknown")], ["https://jobs.example/unknown"]),
    ([{"type": "url", "offset": 0, "length": 26, "url": "https://jobs.example/offer"}], []),
    ([], []),
])
def test_unidentifiable_legacy_offer_gets_an_explanation_without_work(cv, entities, lookups):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(replied=_legacy_offer(*entities)))

    assert cv.sent == [CV_UNKNOWN_OFFER]
    assert "match" not in cv.events
    assert cv.offers.url_lookups == lookups


def test_plain_text_url_is_not_used_for_legacy_lookup(cv):
    legacy = {**_legacy_offer(), "text": "Ver https://jobs.example/offer"}

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(replied=legacy))

    assert cv.sent == [CV_UNKNOWN_OFFER] and cv.offers.url_lookups == []


def test_command_without_reply_explains_how_to_use_it(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(replied=None))

    assert cv.sent == [CV_NEEDS_REPLY]
    assert "match" not in cv.events


@pytest.mark.parametrize("author", [{"id": 42, "is_bot": False}, {"id": 555, "is_bot": True}, None])
def test_reply_to_a_message_not_sent_by_the_bot_does_no_work(cv, author):
    replied = {k: v for k, v in OFFER_MESSAGE.items() if k != "from"}
    if author is not None:
        replied["from"] = author

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(replied=replied))

    assert cv.sent == [CV_UNKNOWN_OFFER]
    assert "match" not in cv.events


@pytest.mark.parametrize("sender", [7, None])
def test_command_from_another_sender_is_ignored(cv, sender):
    update = _cv_update(sender=sender or 42)
    if sender is None:
        del update["message"]["from"]

    cv.http.post("/webhooks/telegram", headers=SECRET, json=update)

    assert cv.events == [] and cv.sent == []


def test_wrong_secret_does_not_generate(cv):
    response = cv.http.post("/webhooks/telegram", headers={SECRET_HEADER: "wrong"}, json=REPLY_COMMAND)

    assert response.status_code == 401
    assert cv.events == [] and cv.sent == []


def test_resent_update_starts_one_generation(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(update_id=60))
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(update_id=60))

    assert cv.events.count("match") == 1 and cv.sent == [CV_ACCEPTED]


def test_live_claim_is_reported_without_starting_another_generation(cv):
    from datetime import datetime, timezone

    assert cv.use_case.prepare("offer", datetime.now(timezone.utc)).action == "generate"

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(update_id=61))

    assert cv.sent == [CV_IN_PROGRESS]
    assert "match" not in cv.events and cv.state.pdf_calls == []


def test_ready_version_is_resent_without_llm(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(update_id=62))
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(update_id=63))

    assert cv.sent == [CV_ACCEPTED, CV_ACCEPTED]
    assert cv.events.count("match") == cv.events.count("tailor") == cv.events.count("render") == 1
    assert len(cv.state.pdf_calls) == 2


def test_missing_inputs_are_reported_at_once_without_scheduling(cv, caplog):
    cv.state.profile = None

    with caplog.at_level(logging.ERROR):
        response = cv.http.post("/webhooks/telegram", headers=SECRET, json=REPLY_COMMAND)

    assert response.status_code == 200
    assert cv.sent == [CV_NOT_STARTED]
    assert "match" not in cv.events


@pytest.mark.parametrize("failing", ["tailor", "send_pdf"])
def test_background_failure_is_reported_without_details(cv, caplog, failing):
    cv.state.fail = failing

    with caplog.at_level(logging.ERROR):
        response = cv.http.post("/webhooks/telegram", headers=SECRET, json=REPLY_COMMAND)

    assert response.status_code == 200
    assert cv.sent == [CV_ACCEPTED, CV_FAILED]
    assert "SECRET-CV-TEXT" not in caplog.text and "SECRET-CV-TEXT" not in "".join(cv.sent)
    assert "RuntimeError" in caplog.text


def test_help_points_to_the_cv_button():
    assert "Ajustar CV" in HELP and "/ajustar_cv" not in HELP


# --- «Ajustar CV» button under each offer message ---

def _cv_button(*, update_id=70, offer=OFFER_MESSAGE, chat=None, sender=42, callback_id="cb-cv", data="cv:adjust"):
    message = {**offer, "chat": chat or offer["chat"]}
    query = {"from": {"id": sender, "is_bot": False}, "data": data, "message": message}
    if callback_id is not None:
        query["id"] = callback_id
    return {"update_id": update_id, "callback_query": query}


def test_cv_button_acknowledges_answers_and_replies_with_the_pdf_to_the_offer(cv):
    response = cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button())

    assert response.status_code == 200
    assert cv.sent == [CV_ACCEPTED]
    assert cv.answers == [("cb-cv", CV_BUTTON_ACCEPTED)]
    assert cv.events.index("answer") < cv.events.index("ack") < cv.events.index("match")
    assert cv.state.pdf_calls == [("42", 91, b"%PDF-cv", "offer")]
    assert cv.offers.url_lookups == []


def test_applied_button_records_once_and_confirms_repeated_press():
    app = FastAPI()
    messenger = Messenger()
    calls, answers = [], []

    class Applied:
        def mark_applied(self, posting_id):
            calls.append(posting_id)
            return len(calls) == 1

    class ButtonChat(NoPreferences):
        def answer(self, callback_id, text):
            answers.append((callback_id, text))

    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=messenger,
                         build_profile=Builder(), resend_pending=Builder(),
                         applied_proposals=Applied(), preferences_chat=ButtonChat(),
                         cv_generator=NoCv(), offer_messages=NoCv(), preferences=NoPreferences())
    client = TestClient(app)
    button = _cv_button(data="applied:offer")
    assert client.post("/webhooks/telegram", headers=SECRET, json=button).status_code == 200
    button["update_id"] += 1
    assert client.post("/webhooks/telegram", headers=SECRET, json=button).status_code == 200

    assert calls == ["offer", "offer"]
    assert answers == [("cb-cv", "Postulación registrada"), ("cb-cv", "Ya estaba registrada")]
    assert messenger.sent == ["✅ Registré esta propuesta como aplicada.",
                              "Esta propuesta ya estaba marcada como aplicada."]


def test_applied_button_ignores_other_chats_and_invalid_ids():
    app = FastAPI()
    calls = []

    class Applied:
        def mark_applied(self, posting_id):
            calls.append(posting_id)
            return True

    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=Messenger(),
                         build_profile=Builder(), resend_pending=Builder(), applied_proposals=Applied(),
                         **CV_PORTS)
    client = TestClient(app)
    for button in (_cv_button(data="applied:../bad", update_id=80),
                   _cv_button(data="applied:offer", sender=99, update_id=81)):
        client.post("/webhooks/telegram", headers=SECRET, json=button)

    assert calls == []


def test_applied_button_reports_missing_offer_and_firestore_failure():
    app = FastAPI()
    messenger = Messenger()
    answers = []

    class Applied:
        def mark_applied(self, posting_id):
            if posting_id == 'missing':
                raise LookupError('missing')
            raise RuntimeError('private details')

    class ButtonChat(NoPreferences):
        def answer(self, callback_id, text):
            answers.append(text)

    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=messenger,
                         build_profile=Builder(), resend_pending=Builder(), applied_proposals=Applied(),
                         cv_generator=NoCv(), offer_messages=NoCv(), preferences=NoPreferences(),
                         preferences_chat=ButtonChat())
    client = TestClient(app)
    client.post("/webhooks/telegram", headers=SECRET, json=_cv_button(data="applied:missing", update_id=83))
    client.post("/webhooks/telegram", headers=SECRET, json=_cv_button(data="applied:offer", update_id=84))

    assert answers == ["Oferta no disponible", CALLBACK_FAILED]
    assert messenger.sent == ["No encontré esa oferta para registrar la postulación.",
                              "No pude registrar tu postulación. Inténtalo de nuevo."]
    assert 'private details' not in ' '.join(messenger.sent)


def test_cv_button_on_an_offer_whose_record_is_missing_uses_its_unique_link(cv):
    legacy = _legacy_offer(_link("https://jobs.example/offer"))

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(offer=legacy))

    assert cv.sent == [CV_ACCEPTED]
    assert cv.state.pdf_calls == [("42", 80, b"%PDF-cv", "offer")]


def test_cv_button_on_an_inaccessible_offer_message_uses_the_record(cv):
    # Telegram sends only chat, message_id and date 0 for messages it can no longer deliver.
    inaccessible = {"message_id": 91, "date": 0, "chat": {"id": 42, "type": "private"}}

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(offer=inaccessible))

    assert cv.sent == [CV_ACCEPTED] and cv.state.pdf_calls == [("42", 91, b"%PDF-cv", "offer")]


def test_cv_button_on_an_unidentifiable_offer_explains_without_work(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(offer=_legacy_offer()))

    assert cv.sent == [CV_UNKNOWN_OFFER]
    assert cv.answers == [("cb-cv", CV_BUTTON_ACCEPTED)]
    assert "match" not in cv.events


def test_cv_button_pressed_twice_starts_one_generation(cv):
    from datetime import datetime, timezone

    assert cv.use_case.prepare("offer", datetime.now(timezone.utc)).action == "generate"

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(update_id=71))

    assert cv.sent == [CV_IN_PROGRESS]
    assert cv.answers == [("cb-cv", CV_BUTTON_ACCEPTED)]
    assert "match" not in cv.events


def test_repeated_cv_button_update_is_handled_once(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(update_id=72))
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(update_id=72))

    assert cv.events.count("match") == 1 and cv.answers == [("cb-cv", CV_BUTTON_ACCEPTED)]


def test_cv_button_with_missing_inputs_is_answered_and_reported(cv):
    cv.state.profile = None

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button())

    assert cv.sent == [CV_NOT_STARTED]
    assert cv.answers == [("cb-cv", CV_BUTTON_ACCEPTED)]
    assert "match" not in cv.events


def test_cv_button_background_failure_points_back_to_the_button(cv):
    cv.state.fail = "tailor"

    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button())

    assert cv.sent == [CV_ACCEPTED, CV_FAILED]
    assert "Ajustar CV" in CV_FAILED and "SECRET-CV-TEXT" not in "".join(cv.sent)


@pytest.mark.parametrize("update", [
    _cv_button(sender=7),
    _cv_button(chat={"id": 42, "type": "group"}),
    _cv_button(chat={"id": 99, "type": "private"}, sender=99),
    _cv_button(offer={k: v for k, v in OFFER_MESSAGE.items() if k != "message_id"}),
])
def test_cv_button_outside_the_private_chat_does_no_work(cv, update):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=update)

    assert cv.answers == [("cb-cv", CALLBACK_INVALID)]
    assert cv.sent == [] and cv.events == ["answer"]


def test_cv_button_without_callback_id_is_ignored(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_button(callback_id=None))

    assert cv.answers == [] and cv.sent == [] and cv.events == []


def test_cv_command_still_works_for_offers_without_button(cv):
    cv.http.post("/webhooks/telegram", headers=SECRET, json=_cv_update(update_id=73))

    assert cv.sent == [CV_ACCEPTED] and cv.answers == []
    assert cv.state.pdf_calls == [("42", 91, b"%PDF-cv", "offer")]


# --- /preferencias and its inline buttons ---

from datetime import datetime, timezone  # noqa: E402

from job_contracts import SearchPreferences  # noqa: E402

from job_agent.application.manage_search_preferences import ManageSearchPreferences  # noqa: E402
from job_agent.domain.policies import SearchBudgets  # noqa: E402
from job_agent.domain.preference_edits import PreferenceEdit, PreferenceOperation  # noqa: E402
from job_agent.entrypoints.telegram import (  # noqa: E402
    PREFERENCES_FAILED, PREFERENCES_REVIEWING,
)
from tests.fakes import MemoryProfileStore, MemorySearchSettings  # noqa: E402

DRAFT_ID = f"{1:032x}"


class Interpreter:
    def __init__(self, events):
        self.events, self.calls, self.error = events, [], None

    def interpret(self, current, request):
        self.events.append("interpret")
        self.calls.append(request)
        if self.error:
            raise self.error
        return PreferenceEdit(operations=[
            PreferenceOperation(action="add", field="keywords_include", values=["Go"], explanation="")])


class RecordingChat:
    def __init__(self, events):
        self.events, self.calls = events, []

    def show(self, view):
        self.events.append("show")
        self.calls.append("show")

    def proposal(self, proposal):
        self.events.append("proposal")
        self.calls.append(("proposal", proposal.kind))

    def resolved(self, message_id, resolution):
        self.calls.append(("resolved", message_id, resolution.status))

    def answer(self, callback_id, text):
        self.calls.append(("answer", callback_id, text))


@pytest.fixture
def pref():
    from types import SimpleNamespace

    events = []
    store = MemorySearchSettings()
    store.seed(SearchPreferences(keywords_include=["Django"]), lambda p: None, force=False,
               now=datetime.now(timezone.utc))
    ids = iter(range(1, 100))
    interpreter = Interpreter(events)
    use_case = ManageSearchPreferences(store=store, profiles=MemoryProfileStore(), budgets=SearchBudgets(),
                                       interpreter=interpreter, new_id=lambda: f"{next(ids):032x}")
    chat = RecordingChat(events)

    class RecordingMessenger:
        sent = []

        def send_text(self, text):
            events.append("ack" if text == PREFERENCES_REVIEWING else "reply")
            self.sent.append(text)

    messenger = RecordingMessenger()
    builder = Builder()
    app = FastAPI()
    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=messenger, build_profile=builder,
                         resend_pending=Builder(), cv_generator=NoCv(), offer_messages=NoCv(),
                         preferences=use_case, preferences_chat=chat)
    return SimpleNamespace(http=TestClient(app), events=events, store=store, interpreter=interpreter, chat=chat,
                           messenger=messenger, builder=builder, use_case=use_case)


def message(text, *, update_id=80, chat=None, sender=42):
    return {"update_id": update_id, "message": {
        "message_id": 400 + update_id, "chat": chat or {"id": 42, "type": "private"},
        "from": {"id": sender, "is_bot": False}, "text": text}}


def callback(*, update_id=90, chat=42, chat_type="private", sender=42, data=f"pref:apply:{DRAFT_ID}",
             callback_id="cb-1"):
    query = {"from": {"id": sender, "is_bot": False}, "data": data,
             "message": {"message_id": 300, "chat": {"id": chat, "type": chat_type}}}
    if callback_id is not None:
        query["id"] = callback_id
    return {"update_id": update_id, "callback_query": query}


def test_preferences_without_text_is_synchronous_and_deterministic(pref):
    response = pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/preferencias"))
    assert response.status_code == 200 and pref.interpreter.calls == [] and pref.chat.calls == ["show"]
    assert pref.store.drafts == {} and pref.messenger.sent == []


def test_preferences_with_text_acknowledges_then_proposes(pref):
    pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/preferencias@job_bot quiero Go"))
    assert pref.events.index("ack") < pref.events.index("interpret") < pref.events.index("proposal")
    assert pref.interpreter.calls == ["quiero Go"]
    assert pref.messenger.sent == [PREFERENCES_REVIEWING]
    assert pref.chat.calls == [("proposal", "draft")]
    assert list(pref.store.drafts) == [DRAFT_ID]


def test_preference_failure_is_reported_without_details(pref, caplog):
    pref.interpreter.error = RuntimeError("SECRET-REQUEST-TEXT")

    with caplog.at_level(logging.ERROR):
        response = pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/preferencias quiero Go"))

    assert response.status_code == 200
    assert pref.messenger.sent == [PREFERENCES_REVIEWING, PREFERENCES_FAILED]
    assert "SECRET-REQUEST-TEXT" not in caplog.text and "RuntimeError" in caplog.text
    assert pref.store.drafts == {}


@pytest.mark.parametrize("update", [
    message("/preferencias", chat={"id": 42, "type": "group"}),
    message("/preferencias quiero Go", sender=7),
    message("/preferencias", chat={"id": 7, "type": "private"}),
])
def test_preferences_outside_the_private_chat_are_ignored(pref, update):
    pref.http.post("/webhooks/telegram", headers=SECRET, json=update)
    assert pref.chat.calls == [] and pref.messenger.sent == [] and pref.interpreter.calls == []


def _propose(pref):
    pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/preferencias quiero Go", update_id=1))
    pref.chat.calls.clear()


@pytest.mark.parametrize("update", [
    callback(chat=7), callback(sender=7), callback(chat_type="group"), callback(data="pref:apply:../x"),
    callback(data="other"), callback(data=f"pref:apply:{'A' * 32}"), callback(data=f"pref:apply:{DRAFT_ID}\n"),
    callback(data=f"pref:delete:{DRAFT_ID}"), callback(data=None),
])
def test_invalid_callbacks_change_nothing(pref, update):
    _propose(pref)

    response = pref.http.post("/webhooks/telegram", headers=SECRET, json=update)

    assert response.status_code == 200
    assert pref.store.drafts[DRAFT_ID].status == "PENDING" and pref.store.prefs.version == 1
    assert pref.chat.calls == [("answer", "cb-1", "Acción no válida")]


def test_invalid_callback_without_id_is_ignored(pref):
    _propose(pref)
    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(chat=7, callback_id=None))
    assert pref.chat.calls == [] and pref.store.drafts[DRAFT_ID].status == "PENDING"


def test_apply_button_applies_once_and_edits_message(pref):
    _propose(pref)

    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(update_id=91, callback_id="cb-1"))
    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(update_id=92, callback_id="cb-2"))

    assert pref.store.prefs.version == 2 and pref.store.prefs.keywords_include == ["Django", "Go"]
    assert pref.store.drafts[DRAFT_ID].status == "APPLIED"
    assert pref.chat.calls == [
        ("answer", "cb-1", "Aplicado"), ("resolved", 300, "applied"),
        ("answer", "cb-2", "Esta propuesta ya estaba resuelta"), ("resolved", 300, "already_resolved"),
    ]


def test_repeated_callback_update_is_handled_once(pref):
    _propose(pref)
    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(update_id=93))
    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(update_id=93))
    assert pref.chat.calls == [("answer", "cb-1", "Aplicado"), ("resolved", 300, "applied")]


def test_cancel_button_changes_no_preferences(pref):
    _propose(pref)
    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(data=f"pref:cancel:{DRAFT_ID}"))
    assert pref.store.prefs.version == 1 and pref.store.drafts[DRAFT_ID].status == "CANCELLED"
    assert pref.chat.calls == [("answer", "cb-1", "Cancelado"), ("resolved", 300, "cancelled")]


def test_unknown_draft_is_answered_without_changes(pref):
    pref.http.post("/webhooks/telegram", headers=SECRET, json=callback(data=f"pref:apply:{'f' * 32}"))
    assert pref.store.prefs.version == 1
    assert pref.chat.calls == [("answer", "cb-1", "Esta propuesta ya no es válida"), ("resolved", 300, "not_found")]


def test_callback_failure_is_answered_without_details(pref, caplog, monkeypatch):
    def boom(*args):
        raise RuntimeError("SECRET-DRAFT")

    monkeypatch.setattr(pref.use_case, "resolve", boom)
    with caplog.at_level(logging.ERROR):
        response = pref.http.post("/webhooks/telegram", headers=SECRET, json=callback())

    assert response.status_code == 200
    assert pref.chat.calls == [("answer", "cb-1", "No pude completar la acción")]
    assert "SECRET-DRAFT" not in caplog.text and "RuntimeError" in caplog.text


def test_build_profile_rebuilds_plan(pref):
    pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/build_profile"))
    assert pref.builder.calls == 1
    assert [q.keywords for q in pref.store.plan.search.queries] == ["Django"]
    assert pref.messenger.sent == [BUILDING]


def test_build_profile_without_keywords_explains_the_plan_was_kept(pref):
    pref.store.prefs = SearchPreferences(version=1)
    pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/build_profile"))
    assert pref.store.plan is None
    assert pref.messenger.sent == [
        BUILDING, "Tu perfil se guardó, pero no hay palabras clave para buscar; agrega alguna con /preferencias."]


def test_build_profile_reports_a_failed_rebuild_and_keeps_the_profile(pref, monkeypatch, caplog):
    def boom():
        raise RuntimeError("SECRET-PLAN")

    monkeypatch.setattr(pref.use_case, "rebuild_plan", boom)
    with caplog.at_level(logging.ERROR):
        pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/build_profile"))
    assert pref.builder.calls == 1
    assert pref.messenger.sent == [BUILDING, "Tu perfil se guardó, pero no pude actualizar el plan de búsqueda."]
    assert "SECRET-PLAN" not in caplog.text


def test_build_profile_superseded_rebuild_is_silent(pref, monkeypatch):
    from job_agent.application.preference_models import RebuildResult

    monkeypatch.setattr(pref.use_case, "rebuild_plan", lambda: RebuildResult(status="superseded"))
    pref.http.post("/webhooks/telegram", headers=SECRET, json=message("/build_profile"))
    assert pref.builder.calls == 1
    assert pref.messenger.sent == [BUILDING]


def test_failed_build_does_not_rebuild_the_plan():
    preferences = NoPreferences()
    app = FastAPI()
    add_telegram_webhook(app, secret="hook-secret", chat_id="42", messenger=Messenger(),
                         build_profile=Builder(error=RuntimeError("x")), resend_pending=Builder(),
                         cv_generator=NoCv(), offer_messages=NoCv(), preferences=preferences,
                         preferences_chat=NoPreferences())
    TestClient(app).post("/webhooks/telegram", headers=SECRET, json=_update("/build_profile"))
    assert preferences.rebuilds == 0


def test_help_lists_the_preferences_command():
    assert "\n/preferencias — ver o cambiar el tipo de ofertas que busco" in HELP


from tests.application.test_cv_review import review_harness
from tests.application.test_generate_tailored_cv import harness


@pytest.fixture
def review_webhook(review_harness):
    from types import SimpleNamespace
    h = review_harness
    h.previews, h.proposals, h.answers = [], [], []
    def preview(*args):
        h.previews.append(args)
        return SimpleNamespace(preview_message_id=100+len(h.previews), markdown_message_id=200+len(h.previews))
    def proposal(*args):
        h.proposals.append(args)
        return 301
    h.use_case.delivery.send_preview = preview
    h.use_case.delivery.send_edit_proposal = proposal
    h.messenger = Messenger()
    app=FastAPI()
    add_telegram_webhook(app, secret='hook-secret', chat_id='42', messenger=h.messenger,
        build_profile=Builder(), resend_pending=Builder(), cv_generator=h.use_case,
        cv_reviews=h.service, offer_messages=SimpleNamespace(resolve=lambda *a:'offer'),
        preferences=NoPreferences(), preferences_chat=SimpleNamespace(answer=lambda *a:h.answers.append(a)), bot_id=123)
    h.web=TestClient(app)
    def post(text=None, data=None, reply=None, user=42, kind='private'):
        message={'message_id':90,'chat':{'id':42,'type':kind},'from':{'id':user}}
        if data:
            payload={'callback_query':{'id':'callback','data':data,'from':{'id':user},'message':message}}
        else:
            message['text']=text
            if reply is not None: message['reply_to_message']={'message_id':reply,'from':{'id':123,'is_bot':True}}
            payload={'message':message}
        return h.web.post('/webhooks/telegram', headers=SECRET,json=payload)
    h.post=post
    return h


def test_adjust_cv_publishes_review_before_pdf_and_explicit_approval_delivers(review_webhook):
    h=review_webhook
    h.post('/ajustar_cv',reply=91)
    assert len(h.previews)==1 and 'render' not in h.events
    _,_,review,revision,summary,markdown=h.previews[-1]
    assert markdown == h.state.tailored.resume_markdown
    h.post(data=f'cv:approve:{review}:{revision}')
    assert h.events.count('render') == 1
    assert h.answers[0][1] == 'Procesando revisión…'
    h.post(data=f'cv:approve:{review}:{revision}')
    assert h.events.count('send_pdf') == 1


def test_correction_reply_confirm_stale_approval_reject_cancel(review_webhook):
    from job_agent.application.cv_review_models import CvEditProposal, TextReplacement
    from types import SimpleNamespace
    from job_agent.adapters.persistence.firestore_profile_corrections import FirestoreProfileCorrections
    h=review_webhook
    h.client.collection('profiles').document('current').set(h.state.profile.model_dump())
    h.service.corrections=FirestoreProfileCorrections(h.client)
    h.state.tailored=h.state.tailored.model_copy(update={'resume_markdown':'# CV\nOld headline\nPython'})
    h.service.interpreter=SimpleNamespace(propose=lambda *a:CvEditProposal(replacements=[TextReplacement(old_text='Old headline',new_text='New headline')],fact_operations=[],explanation='Local'))
    h.post('/ajustar_cv',reply=91)
    review,revision=h.previews[-1][2:4]
    h.post('Mejora el titular',reply=101)
    assert len(h.proposals)==1
    proposal=h.proposals[-1][3]
    h.post(data=f'cv:confirm:{review}:{proposal}')
    assert len(h.previews)==2
    h.post(data=f'cv:approve:{review}:{revision}')
    assert 'render' not in h.events
    assert any('cambió' in m for m in h.messenger.sent)
    h.post(data=f'cv:edit:{review}')
    assert any('Responde' in m for m in h.messenger.sent)
    h.post(data=f'cv:cancel:{review}')
    assert h.reviews.load(review,'42').status=='CANCELLED'


@pytest.mark.parametrize('user,kind,reply', [(99,'private',101),(42,'group',101),(42,'private',999)])
def test_review_replies_require_owner_and_mapped_preview(review_webhook,user,kind,reply):
    h=review_webhook
    h.post('/ajustar_cv',reply=91)
    h.post('Elimina inglés',reply=reply,user=user,kind=kind)
    assert h.proposals==[]


def test_legacy_pdf_command_reopens_ready_version(review_webhook):
    h=review_webhook
    h.post('/ajustar_cv',reply=91)
    review,revision=h.previews[-1][2:4]
    h.post(data=f'cv:approve:{review}:{revision}')
    h.post('/corregir_cv',reply=102)
    assert len(h.previews)==2
    assert h.previews[-1][2] != review
    assert h.events.count('tailor') == 1


def test_factual_reply_shows_future_profile_change_then_rejects_without_mutation(review_webhook):
    from job_agent.application.cv_review_models import CvEditProposal, TextReplacement
    from job_agent.domain.cv_corrections import FactOperation
    from job_agent.adapters.persistence.firestore_profile_corrections import FirestoreProfileCorrections
    from types import SimpleNamespace
    h=review_webhook
    h.client.collection('profiles').document('current').set(h.state.profile.model_dump())
    h.service.corrections=FirestoreProfileCorrections(h.client)
    h.state.tailored=h.state.tailored.model_copy(update={'resume_markdown':'# CV\nEnglish B2\nPython'})
    h.service.interpreter=SimpleNamespace(propose=lambda *a:CvEditProposal(replacements=[TextReplacement(old_text='English B2\n',new_text='')],fact_operations=[FactOperation('remove_language','English')],explanation='No hablo inglés'))
    h.post('/ajustar_cv',reply=91)
    review=h.previews[-1][2]
    h.post('No hablo inglés',reply=101)
    assert len(h.proposals)==1
    assert h.proposals[-1][-1]=='global'
    assert 'futuros CV' in h.proposals[-1][-2]
    assert 'Eliminar idioma: english' in h.proposals[-1][-2]
    proposal=h.proposals[-1][3]
    h.post(data=f'cv:reject:{review}:{proposal}')
    assert h.service.corrections.load().version==0
    assert h.reviews.load_proposal(review,proposal).status=='reject'


@pytest.mark.parametrize('user,kind', [(99,'private'),(42,'group')])
def test_review_buttons_reject_other_users_and_groups(review_webhook,user,kind):
    h=review_webhook
    h.post('/ajustar_cv',reply=91)
    review,revision=h.previews[-1][2:4]
    h.post(data=f'cv:approve:{review}:{revision}',user=user,kind=kind)
    assert 'render' not in h.events
    assert h.answers[-1][1] == CALLBACK_INVALID


def test_delivered_pdf_correction_button_opens_a_new_review(review_webhook):
    h=review_webhook
    h.post('/ajustar_cv',reply=91)
    review,revision=h.previews[-1][2:4]
    h.post(data=f'cv:approve:{review}:{revision}')
    h.web.post('/webhooks/telegram',headers=SECRET,json={'callback_query':{
        'id':'correct','data':f'cv:correct:{review}','from':{'id':42},
        'message':{'message_id':102,'chat':{'id':42,'type':'private'}}}})
    assert len(h.previews)==2
    assert h.previews[-1][2]!=review
    assert h.events.count('tailor') == h.events.count('render') == 1


def test_old_buttonless_pdf_command_uses_saved_markdown_without_models(review_webhook):
    from tests.application.test_generate_tailored_cv import NOW
    h=review_webhook
    prepared=h.use_case.prepare('offer',NOW)
    h.use_case.execute(prepared,'42',91)
    assert prepared.key.review_id is None
    h.post('/corregir_cv',reply=102)
    assert len(h.previews)==1
    assert h.previews[0][-1]==h.state.tailored.resume_markdown
    assert h.events.count('tailor') == h.events.count('render') == 1


def test_legacy_lookup_missing_index_explains_retry_without_changing_state(review_webhook,monkeypatch):
    from google.api_core.exceptions import FailedPrecondition
    h=review_webhook
    before=dict(h.client.docs)
    def missing_index(*a,**kw): raise FailedPrecondition('private backend URL and query')
    monkeypatch.setattr(h.client,'collection_group',missing_index)
    h.post('/corregir_cv',reply=500)
    assert h.client.docs==before
    assert any('índice' in m and '/corregir_cv' in m for m in h.messenger.sent)
    assert not any('private backend' in m for m in h.messenger.sent)


def test_adjust_button_answers_before_notification_or_review_work(review_webhook,monkeypatch):
    from job_agent.scoring.models import ADJUST_CV_CALLBACK
    h=review_webhook
    def notify(text):
        assert h.answers == [('callback',CV_BUTTON_ACCEPTED)]
        h.messenger.sent.append(text)
    monkeypatch.setattr(h.messenger,'send_text',notify)
    h.post(data=ADJUST_CV_CALLBACK)
    assert len(h.previews)==1
    assert h.messenger.sent


def test_legacy_adjust_button_answers_before_slow_notification(cv):
    cv.http.post('/webhooks/telegram',headers=SECRET,json=_cv_button())
    assert cv.events.index('answer') < cv.events.index('ack')
