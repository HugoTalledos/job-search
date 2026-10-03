import importlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore, storage

from job_agent.config import Config, MatchingConfig
from job_agent.scoring.models import PostingEnrichment, ScoreResult, TelegramMessageRef
from tests.adapters.test_firebase_cv_artifacts import Client as StorageClient


class Snapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self.data = data

    def to_dict(self):
        return self.data


class Document:
    def __init__(self, client, collection, doc_id):
        self.client, self.collection, self.id = client, collection, doc_id

    def get(self):
        return Snapshot(self.id, self.client.docs.get(f"{self.collection}/{self.id}"))

    def update(self, fields):
        self.client.docs[f"{self.collection}/{self.id}"].update(fields)

    def set(self, fields):
        self.client.docs[f"{self.collection}/{self.id}"] = fields


class Collection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def document(self, doc_id):
        return Document(self.client, self.name, doc_id)

    def stream(self):
        return (
            Snapshot(path.split("/", 1)[1], data)
            for path, data in self.client.docs.items() if path.startswith(f"{self.name}/")
        )


class FakeFirestoreClient:
    def __init__(self):
        self.docs = {}

    def collection(self, name):
        return Collection(self, name)


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_WEBHOOK_API_KEY", "fixture-key")
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "fixture-project")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-fixture-key")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-fixture-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "telegram-secret")
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", "private-bucket")
    monkeypatch.setattr(firestore, "Client", lambda **kwargs: FakeFirestoreClient())
    # Never reach real Cloud Storage: keep the artifact adapter, replace only its client.
    monkeypatch.setattr(storage, "Client", lambda **kwargs: StorageClient())
    module = importlib.import_module("job_agent.webhook")
    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    monkeypatch.setattr(module, "load_config", lambda: Config(), raising=False)
    return module


def test_webhook_requires_api_key_at_startup(webhook, monkeypatch):
    monkeypatch.delenv("JOB_AGENT_WEBHOOK_API_KEY")

    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        webhook.build_webhook_app()


def test_webhook_requires_firestore_project_at_startup(webhook, monkeypatch):
    monkeypatch.delenv("FIRESTORE_PROJECT_ID")

    with pytest.raises(ValueError, match="FIRESTORE_PROJECT_ID"):
        webhook.build_webhook_app()


def test_webhook_requires_openrouter_key_at_startup(webhook, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        webhook.build_webhook_app()


@pytest.mark.parametrize("missing", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET",
                                     "FIREBASE_STORAGE_BUCKET"])
def test_webhook_requires_telegram_credentials_at_startup(webhook, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        webhook.build_webhook_app()


def test_webhook_wires_firestore_and_jev_without_rescoring(webhook, monkeypatch, profile, job):
    projects = []
    client = FakeFirestoreClient()
    client.docs["profiles/current"] = profile.model_dump()
    client.docs["job_postings/original"] = {"job": job.model_dump(), "status": "PENDING"}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: projects.append(kwargs["project"]) or client)
    scored = []
    enriched = []
    notified = []

    class FakeScorer:
        def score(self, candidate, posting):
            scored.append((candidate, posting))
            return ScoreResult(score=80, confidence=0.8, model="typesafe/jev-1.13")

    monkeypatch.setattr(webhook, "JevScoringTool", lambda api_key: FakeScorer())

    class FakeEnricher:
        def enrich(self, posting):
            enriched.append(posting)
            return PostingEnrichment("English B2", "USD 2,000 - 3,000")

    class FakeNotifier:
        def notify(self, posting, result, enrichment):
            notified.append((posting, result.score, enrichment.required_language))
            return TelegramMessageRef("42", 91)

    monkeypatch.setattr(webhook, "JevOfferEnricher", lambda api_key: FakeEnricher(), raising=False)
    monkeypatch.setattr(webhook, "TelegramOfferNotifier", lambda token, chat_id: FakeNotifier(), raising=False)

    app = webhook.build_webhook_app()
    with TestClient(app) as http:
        first = http.post("/webhooks/inference", headers={"X-API-Key": "fixture-key"})
        second = http.post("/webhooks/inference", headers={"X-API-Key": "fixture-key"})

    assert projects == ["fixture-project"]
    assert first.status_code == second.status_code == 200
    assert first.content == second.content == b""
    assert scored == [(profile, job)]
    assert enriched == [job]
    assert notified == [(job, 80, "English B2")]
    assert client.docs["telegram_offer_messages/42_91"]["posting_id"] == "original"
    assert client.docs["job_postings/original"]["status"] == "NOTIFIED"
    assert client.docs["job_postings/original"]["score"] == 80
    assert client.docs["job_postings/original"]["required_language"] == "English B2"
    assert client.docs["job_postings/original"]["job"] == job.model_dump()


def test_webhook_uses_configured_notification_threshold(webhook, monkeypatch, profile, job):
    client = FakeFirestoreClient()
    client.docs["profiles/current"] = profile.model_dump()
    client.docs["job_postings/low"] = {"job": job.model_dump(), "status": "PENDING"}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    monkeypatch.setattr(webhook, "load_config", lambda: Config(matching=MatchingConfig(min_score_to_notify=90)))
    monkeypatch.setattr(webhook, "JevOfferEnricher", lambda key: type("Enricher", (), {"enrich": lambda self, job: PostingEnrichment()})(), raising=False)
    monkeypatch.setattr(webhook, "JevScoringTool", lambda key: type("Scorer", (), {"score": lambda self, profile, job: ScoreResult(80, 0.8, "typesafe/jev-1.13")})())
    sent = []
    monkeypatch.setattr(webhook, "TelegramOfferNotifier", lambda token, chat_id: type("Notifier", (), {"notify": lambda self, *args: sent.append(args) or TelegramMessageRef("42", 91)})(), raising=False)

    response = TestClient(webhook.build_webhook_app()).post("/webhooks/inference", headers={"X-API-Key": "fixture-key"})

    assert response.status_code == 200
    assert client.docs["job_postings/low"]["status"] == "EVALUATED"
    assert sent == []


def test_webhook_exposes_firestore_credential_failure_at_startup(webhook, monkeypatch):
    def missing_credentials(**kwargs):
        raise ValueError("credentials missing")

    monkeypatch.setattr(webhook.firestore, "Client", missing_credentials)

    with pytest.raises(ValueError, match="credentials missing"):
        webhook.build_webhook_app()


def test_telegram_build_profile_command_stores_profile_for_scoring(webhook, monkeypatch, profile):
    client = FakeFirestoreClient()
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    sent = []
    monkeypatch.setattr(webhook.TelegramNotifier, "send_text", lambda self, text: sent.append(text))

    class FakeRepositories:
        def __init__(self, **kwargs):
            pass

        def list_repositories(self):
            return []

    class FakeInferer:
        def __init__(self, model):
            pass

        def infer(self, resume_text, evidence, preferred_locations):
            return profile

    monkeypatch.setattr(webhook, "GitRepositoryReader", FakeRepositories)
    monkeypatch.setattr(webhook, "LlmProfileInferer", FakeInferer)

    response = TestClient(webhook.build_webhook_app()).post(
        "/webhooks/telegram",
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"update_id": 1, "message": {"chat": {"id": 42}, "text": "/build-profile"}},
    )

    assert response.status_code == 200
    assert sent[0] == "Voy a construir tu nuevo perfil profesional"
    assert "Tu perfil profesional está listo" in sent[1]
    from job_agent.scoring.firestore import FirestoreScoringStore

    assert FirestoreScoringStore(client).load() == profile


def test_telegram_resends_saved_pending_notifications_without_rescoring(webhook, monkeypatch, job):
    client = FakeFirestoreClient()
    saved = {
        "status": "PENDING_NOTIFICATION", "job": job.model_dump(),
        "score": 83, "confidence": 0.7, "score_model": "typesafe/jev-1.13",
        "required_language": "English B2", "salary_range": "$2000",
    }
    client.docs["job_postings/success"] = saved.copy()
    client.docs["job_postings/failure"] = saved.copy()
    client.docs["job_postings/unscored"] = {"status": "PENDING", "job": job.model_dump()}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    messages, sent = [], []
    monkeypatch.setattr(webhook.TelegramNotifier, "send_text", lambda self, text: messages.append(text))

    class FakeOfferNotifier:
        def notify(self, posting, result, enrichment):
            sent.append((result.score, enrichment.required_language))
            if len(sent) == 2:
                raise RuntimeError("Telegram unavailable")
            return TelegramMessageRef("42", 92)

    monkeypatch.setattr(webhook, "TelegramOfferNotifier", lambda token, chat_id: FakeOfferNotifier())

    response = TestClient(webhook.build_webhook_app()).post(
        "/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
        json={"update_id": 21, "message": {"chat": {"id": 42}, "text": "/resend_pending"}},
    )

    assert response.status_code == 200
    assert messages == [
        "Estoy buscando propuestas que hayan quedado pendientes de notificar",
        "Reenvío terminado: 1 notificadas, 1 fallidas.",
    ]
    assert sent == [(83, "English B2"), (83, "English B2")]
    assert client.docs["telegram_offer_messages/42_92"]["posting_id"] == "success"
    assert client.docs["job_postings/success"]["status"] == "NOTIFIED"
    assert client.docs["job_postings/failure"]["status"] == "PENDING_NOTIFICATION"
    assert client.docs["job_postings/unscored"]["status"] == "PENDING"


def test_resend_waits_for_an_active_scoring_run(webhook, monkeypatch, profile, job):
    client = FakeFirestoreClient()
    client.docs["profiles/current"] = profile.model_dump()
    client.docs["job_postings/offer"] = {"status": "PENDING", "job": job.model_dump()}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    monkeypatch.setattr(webhook, "JevScoringTool", lambda key: type("Scorer", (), {
        "score": lambda self, profile, job: ScoreResult(83, 0.7, "typesafe/jev-1.13")})())
    monkeypatch.setattr(webhook, "JevOfferEnricher", lambda key: type("Enricher", (), {
        "enrich": lambda self, job: PostingEnrichment()})())
    started, release = threading.Event(), threading.Event()
    sent = []

    class BlockingNotifier:
        def notify(self, *args):
            sent.append(args)
            started.set()
            release.wait(timeout=3)
            return TelegramMessageRef("42", 91)

    monkeypatch.setattr(webhook, "TelegramOfferNotifier", lambda token, chat_id: BlockingNotifier())
    messages = []
    monkeypatch.setattr(webhook.TelegramNotifier, "send_text", lambda self, text: messages.append(text))
    http = TestClient(webhook.build_webhook_app())

    with ThreadPoolExecutor(max_workers=2) as pool:
        scoring = pool.submit(http.post, "/webhooks/inference", headers={"X-API-Key": "fixture-key"})
        assert started.wait(timeout=2)
        resend = pool.submit(http.post, "/webhooks/telegram",
                             headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
                             json={"update_id": 22, "message": {"chat": {"id": 42}, "text": "/resend_pending"}})
        assert _wait_for_message(messages)
        assert messages[0] == "Estoy buscando propuestas que hayan quedado pendientes de notificar"
        release.set()
        assert scoring.result(timeout=3).status_code == 200
        assert resend.result(timeout=3).status_code == 200

    assert len(sent) == 1
    assert client.docs["job_postings/offer"]["status"] == "NOTIFIED"


def _wait_for_message(messages):
    import time
    for _ in range(100):
        if messages:
            return True
        time.sleep(0.01)
    return False


def test_telegram_cv_reply_generates_and_delivers_through_the_wired_adapters(
    webhook, monkeypatch, tmp_path, profile, job, match, tailored
):
    from job_agent.adapters.persistence.firestore_cv_tracking import FirestoreCvTrackingStore
    from tests.adapters.test_firestore_cv_tracking import Client as TransactionalClient

    resume = tmp_path / "base.md"
    resume.write_text("Base CV: Python")
    monkeypatch.setattr(webhook, "load_config", lambda: Config(resume_path=str(resume)))
    client = FakeFirestoreClient()
    client.docs["profiles/current"] = profile.model_dump()
    client.docs["telegram_offer_messages/42_91"] = {"posting_id": "original"}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    # The scoring fake has no transactions; claims use the transactional fake with the same posting.
    tracking_client = TransactionalClient()
    tracking_client.docs["job_postings/original"] = {"job": job.model_dump(), "status": "NOTIFIED"}
    monkeypatch.setattr(webhook, "FirestoreCvTrackingStore", lambda c: FirestoreCvTrackingStore(tracking_client))
    llm_calls, delivered, messages = [], [], []
    monkeypatch.setattr(webhook, "LlmJobMatcher", lambda model: type("Matcher", (), {
        "score": lambda self, *args: llm_calls.append("match") or match})())
    monkeypatch.setattr(webhook, "LlmResumeTailor", lambda model: type("Tailor", (), {
        "tailor": lambda self, *args: llm_calls.append("tailor") or tailored})())
    monkeypatch.setattr(webhook, "RequiredPdfRenderer", lambda: type("Renderer", (), {
        "render": lambda self, markdown: b"%PDF-wired"})())

    class FakeDelivery:
        def __init__(self, token):
            assert token == "telegram-fixture-token"

        def send_summary(self, chat_id, reply_to, summary):
            delivered.append(("summary", chat_id, reply_to))
            return 301

        def send_pdf(self, chat_id, reply_to, pdf):
            delivered.append(("pdf", chat_id, reply_to, pdf))
            return 302

    monkeypatch.setattr(webhook, "TelegramCvDelivery", FakeDelivery)
    monkeypatch.setattr(webhook.TelegramNotifier, "send_text", lambda self, text: messages.append(text))
    http = TestClient(webhook.build_webhook_app())

    def reply(update_id):
        return http.post("/webhooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
                         json={"update_id": update_id, "message": {
                             "message_id": 500 + update_id, "chat": {"id": 42, "type": "private"},
                             "from": {"id": 42, "is_bot": False}, "text": "/ajustar_cv",
                             "reply_to_message": {"message_id": 91, "from": {"id": 1, "is_bot": True}}}})

    assert reply(31).status_code == 200
    assert reply(32).status_code == 200

    ack = "Estoy ajustando tu CV para esta propuesta. Te enviaré el PDF al terminar."
    assert messages == [ack, ack]
    assert llm_calls == ["match", "tailor"]
    assert delivered.count(("pdf", "42", 91, b"%PDF-wired")) == 2
    tracking = tracking_client.docs["application_tracking/original"]
    assert tracking["stage"] == "CV_READY"


@pytest.mark.parametrize("token, bot_id", [("123456:ABC-def", 123456), ("telegram-fixture-token", None), ("", None)])
def test_bot_id_comes_from_the_token_prefix(webhook, token, bot_id):
    assert webhook._bot_id(token) == bot_id


def test_preferences_flow_end_to_end_through_the_wired_webhook(webhook, monkeypatch):
    import httpx

    from job_agent.domain.preference_edits import PreferenceEdit, PreferenceOperation
    from job_contracts import SearchPreferences
    from tests.adapters.test_firestore_cv_tracking import Client as TransactionalClient

    client = TransactionalClient()
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    monkeypatch.setattr(webhook, "load_config", lambda: Config())
    from job_agent.adapters.persistence import FirestoreSearchSettingsStore
    from job_agent.application.manage_search_preferences import ManageSearchPreferences
    from job_agent.domain.policies import SearchBudgets
    from job_agent.adapters.persistence import FirestoreProfileStore

    ManageSearchPreferences(
        store=FirestoreSearchSettingsStore(client), profiles=FirestoreProfileStore(client),
        budgets=SearchBudgets(max_queries=8, max_roles_from_profile=3, max_details_per_run=20),
    ).seed(SearchPreferences(keywords_include=["Python"]), force=False)
    assert client.docs["settings/search_preferences"]["version"] == 1

    interpreted = []

    class FakeInterpreter:
        def __init__(self, model):
            pass

        def interpret(self, current, request):
            interpreted.append(request)
            return PreferenceEdit(operations=[PreferenceOperation(
                action="add", field="keywords_include", values=["Go"], explanation="")])

    monkeypatch.setattr(webhook, "LlmPreferenceInterpreter", FakeInterpreter)
    telegram_calls, texts = [], []

    def fake_post(url, json=None, **kwargs):
        telegram_calls.append((url.rsplit("/", 1)[1], json))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(webhook.TelegramNotifier, "send_text", lambda self, text: texts.append(text))
    http = TestClient(webhook.build_webhook_app())
    headers = {"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"}
    private = {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False}}

    def send(update_id, text):
        return http.post("/webhooks/telegram", headers=headers, json={
            "update_id": update_id, "message": {"message_id": update_id, **private, "text": text}})

    assert send(1, "/preferencias").status_code == 200
    assert interpreted == []
    method, shown = telegram_calls[-1]
    assert method == "sendMessage" and "Python" in shown["text"] and "versión 1" in shown["text"]

    assert send(2, "/preferencias quiero Go").status_code == 200
    assert interpreted == ["quiero Go"] and texts == ["Revisando tus preferencias…"]
    method, proposal = telegram_calls[-1]
    assert method == "sendMessage"
    apply_data = proposal["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert apply_data.startswith("pref:apply:")

    tap = http.post("/webhooks/telegram", headers=headers, json={"update_id": 3, "callback_query": {
        "id": "cb1", "data": apply_data, "from": private["from"],
        "message": {"message_id": 77, **private}}})

    assert tap.status_code == 200
    plan = client.docs["settings/search_plan"]
    assert {q["keywords"] for q in plan["search"]["queries"]} >= {"Go", "Python"}
    assert plan["preferences_version"] == 2
    assert client.docs["settings/search_preferences"]["version"] == 2
    assert [m for m, _ in telegram_calls[-2:]] == ["answerCallbackQuery", "editMessageText"]
