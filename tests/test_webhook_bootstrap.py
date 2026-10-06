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
        self.path = f"{collection}/{doc_id}"

    def get(self, transaction=None):
        if transaction is not None:
            transaction.reads[f"{self.collection}/{self.id}"] = self.client.revisions.get(f"{self.collection}/{self.id}", 0)
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


from tests.adapters.test_firestore_cv_tracking import Client as TransactionClient


class FakeFirestoreClient(TransactionClient):
    def __init__(self):
        super().__init__()

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


def test_applied_pdf_button_writes_distinct_collection(webhook, monkeypatch, job):
    from tests.adapters.test_firestore_cv_tracking import Client

    client = Client()
    client.docs['job_postings/offer'] = {'job': job.model_dump(mode='json')}
    monkeypatch.setattr(webhook.firestore, 'Client', lambda **kwargs: client)
    messages, answers = [], []
    monkeypatch.setattr(webhook.TelegramNotifier, 'send_text', lambda self, text: messages.append(text))
    monkeypatch.setattr(webhook.TelegramPreferencesChat, 'answer',
                        lambda self, callback_id, text: answers.append((callback_id, text)))
    app = webhook.build_webhook_app()

    response = TestClient(app).post('/webhooks/telegram',
                                    headers={'X-Telegram-Bot-Api-Secret-Token': 'telegram-secret'},
                                    json={'update_id': 100, 'callback_query': {
                                        'id': 'cb-applied', 'data': 'applied:offer', 'from': {'id': 42},
                                        'message': {'message_id': 73, 'chat': {'id': 42, 'type': 'private'}},
                                    }})

    assert response.status_code == 200
    assert client.docs['applied_proposals/offer']['job'] == job.model_dump(mode='json')
    assert answers == [('cb-applied', 'Postulación registrada')]
    assert messages == ['✅ Registré esta propuesta como aplicada.']


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

        def infer(self, resume_text, evidence, preferred_locations, **kwargs):
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
        resend_notice = "Estoy buscando propuestas que hayan quedado pendientes de notificar"
        assert _wait_for_message(messages, resend_notice)
        assert resend_notice in messages
        release.set()
        assert scoring.result(timeout=3).status_code == 200
        assert resend.result(timeout=3).status_code == 200

    assert len(sent) == 1
    assert client.docs["job_postings/offer"]["status"] == "NOTIFIED"


def _wait_for_message(messages, expected=None):
    import time
    for _ in range(100):
        if (expected in messages) if expected is not None else bool(messages):
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
    client = TransactionalClient()
    client.docs["profiles/current"] = profile.model_dump()
    client.docs["telegram_offer_messages/42_91"] = {"posting_id": "original"}
    monkeypatch.setattr(webhook.firestore, "Client", lambda **kwargs: client)
    # The scoring fake has no transactions; claims use the transactional fake with the same posting.
    tracking_client = client
    tracking_client.docs["job_postings/original"] = {"job": job.model_dump(), "status": "NOTIFIED"}
    monkeypatch.setattr(webhook, "FirestoreCvTrackingStore", lambda c: FirestoreCvTrackingStore(tracking_client))
    llm_calls, delivered, messages = [], [], []
    monkeypatch.setattr(webhook, "LlmJobMatcher", lambda model: type("Matcher", (), {
        "score": lambda self, *args: llm_calls.append("match") or match})())
    monkeypatch.setattr(webhook, "LlmResumeTailor", lambda model: type("Tailor", (), {
        "tailor": lambda self, *args: llm_calls.append("tailor") or tailored})())
    monkeypatch.setattr(webhook, "RequiredPdfRenderer", lambda: type("Renderer", (), {
        "render": lambda self, markdown: b"%PDF-wired"})())

    previews = []

    class FakeDelivery:
        def __init__(self, token):
            assert token == "telegram-fixture-token"

        def send_preview(self, chat_id, reply_to, review_id, revision_id, summary, markdown):
            from job_agent.adapters.notifications.telegram_cv import PreviewReceipt
            previews.append((review_id, revision_id, markdown))
            return PreviewReceipt(303, 304)

        def send_summary(self, chat_id, reply_to, summary):
            delivered.append(("summary", chat_id, reply_to))
            return 301

        def send_pdf(self, chat_id, reply_to, pdf, posting_id, *, review_id):
            delivered.append(("pdf", chat_id, reply_to, pdf, posting_id))
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

    assert len(previews) == 2
    assert previews[0] == previews[1]
    assert previews[0][2] == tailored.resume_markdown
    assert delivered == []
    assert llm_calls == ['match', 'tailor']
    review_id, revision_id, _ = previews[-1]
    monkeypatch.setattr(webhook.TelegramPreferencesChat, 'answer', lambda *a: None)
    approval = {'callback_query': {'id':'approve', 'data':f'cv:approve:{review_id}:{revision_id}',
        'from': {'id':42}, 'message': {'message_id':303, 'chat': {'id':42,'type':'private'}}}}
    headers = {'X-Telegram-Bot-Api-Secret-Token':'telegram-secret'}
    assert http.post('/webhooks/telegram',headers=headers,json=approval).status_code == 200
    assert http.post('/webhooks/telegram',headers=headers,json=approval).status_code == 200
    assert delivered.count(('pdf', '42', 303, b'%PDF-wired', 'original')) == 1
    assert tracking_client.docs['application_tracking/original']['stage'] == 'CV_READY'


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


def test_cv_review_production_dependencies_are_wired(webhook, monkeypatch):
    captured={}
    monkeypatch.setattr(webhook,'add_telegram_webhook',lambda app, **kwargs: captured.update(kwargs))
    webhook.build_webhook_app()
    service=captured['cv_reviews']
    from job_agent.application.cv_review import CvReviewService
    from job_agent.adapters.persistence.firestore_cv_reviews import FirestoreCvReviewStore
    from job_agent.adapters.persistence.firestore_profile_corrections import FirestoreProfileCorrections
    from job_agent.adapters.llm.cv_edit import CvEditInterpreter
    assert isinstance(service,CvReviewService)
    assert isinstance(service.reviews,FirestoreCvReviewStore)
    assert isinstance(service.corrections,FirestoreProfileCorrections)
    assert isinstance(service.interpreter,CvEditInterpreter)
    assert service.generation is captured['cv_generator']
    assert service.after_profile_change.__self__ is captured['preferences']
    assert service.after_profile_change.__name__ == 'after_profile_change'
    assert service.notify.__self__ is captured['messenger']


def test_confirmed_language_denial_survives_profile_rebuild_and_later_cv(
    webhook, monkeypatch, tmp_path, profile, job, match, tailored, caplog
):
    from types import SimpleNamespace
    from job_agent.application.cv_review_models import CvEditProposal, TextReplacement
    from job_agent.domain.cv_corrections import FactOperation
    from job_agent.adapters.notifications.telegram_cv import PreviewReceipt
    from job_agent.scoring.firestore import FirestoreScoringStore
    from tests.adapters.test_firestore_cv_tracking import Client

    inferred = profile.model_copy(update={'languages': ['English (B2)', 'Español (nativo)']})
    client = Client()
    client.docs['profiles/current'] = inferred.model_dump()
    for posting_id, message_id in [('original', 91), ('later', 92)]:
        client.docs[f'job_postings/{posting_id}'] = {'job': job.model_dump(), 'status': 'NOTIFIED'}
        client.docs[f'telegram_offer_messages/42_{message_id}'] = {'posting_id': posting_id}
    resume = tmp_path / 'base.md'
    resume.write_text('# Test\nEnglish B2\nPython')
    monkeypatch.setattr(webhook, 'load_config', lambda: Config(resume_path=str(resume)))
    monkeypatch.setattr(webhook.firestore, 'Client', lambda **kwargs: client)
    previews, proposals, rendered, pdfs, profiles_seen, notices = [], [], [], [], [], []
    corrected = '# Test\nPython'
    monkeypatch.setattr(webhook, 'LlmJobMatcher', lambda model: SimpleNamespace(score=lambda *args, **kwargs: match))

    def tailor(*args, **kwargs):
        profiles_seen.append(args[2])
        markdown = '# Test\nEnglish B2\nPython' if len(profiles_seen) == 1 else corrected
        return tailored.model_copy(update={'resume_markdown': markdown})

    monkeypatch.setattr(webhook, 'LlmResumeTailor', lambda model: SimpleNamespace(tailor=tailor))
    monkeypatch.setattr(webhook, 'CvEditInterpreter', lambda model: SimpleNamespace(propose=lambda *args: CvEditProposal(
        replacements=[TextReplacement(old_text='English B2\n', new_text='')],
        fact_operations=[FactOperation('remove_language', 'English')], explanation='Dato confirmado')))
    monkeypatch.setattr(webhook, 'RequiredPdfRenderer', lambda: SimpleNamespace(
        render=lambda markdown: rendered.append(markdown) or markdown.encode()))

    class Delivery:
        def __init__(self, token):
            pass

        def send_preview(self, chat_id, reply_to, review_id, revision_id, summary, markdown):
            previews.append((review_id, revision_id, markdown))
            return PreviewReceipt(300 + len(previews), 400 + len(previews))

        def send_edit_proposal(self, *args):
            proposals.append(args)
            return 501

        def send_summary(self, *args):
            return 601

        def send_pdf(self, chat_id, reply_to, pdf, posting_id, *, review_id):
            pdfs.append(pdf)
            if len(pdfs) == 1:
                raise RuntimeError('sensitive transport detail')
            return 602

    monkeypatch.setattr(webhook, 'TelegramCvDelivery', Delivery)
    monkeypatch.setattr(webhook.TelegramNotifier, 'send_text', lambda self, text: notices.append(text))
    monkeypatch.setattr(webhook.TelegramPreferencesChat, 'answer', lambda *args: None)
    monkeypatch.setattr(webhook, 'GitRepositoryReader', lambda **kwargs: SimpleNamespace(list_repositories=lambda: []))
    rebuilds = []

    def infer(*args, **kwargs):
        rebuilds.append(kwargs.get('corrections'))
        return inferred

    monkeypatch.setattr(webhook, 'LlmProfileInferer', lambda model: SimpleNamespace(infer=infer))
    http = TestClient(webhook.build_webhook_app())
    headers = {'X-Telegram-Bot-Api-Secret-Token': 'telegram-secret'}

    def post(text=None, reply=None, data=None):
        message = {'message_id': 700, 'chat': {'id': 42, 'type': 'private'}, 'from': {'id': 42}}
        if data:
            payload = {'callback_query': {'id': 'callback', 'data': data, 'from': {'id': 42}, 'message': message}}
        else:
            message['text'] = text
            if reply:
                message['reply_to_message'] = {'message_id': reply, 'from': {'id': 1, 'is_bot': True}}
            payload = {'message': message}
        assert http.post('/webhooks/telegram', headers=headers, json=payload).status_code == 200

    post('/ajustar_cv', 91)
    review, first, markdown = previews[-1]
    assert 'English B2' in markdown
    assert rendered == pdfs == []
    post('No hablo inglés; elimina English B2', 301)
    assert 'futuros CV' in proposals[-1][-2]
    assert FirestoreScoringStore(client).load().languages == inferred.languages
    proposal_id = proposals[-1][3]
    post(data=f'cv:confirm:{review}:{proposal_id}')
    _, revision, markdown = previews[-1]
    assert revision != first and markdown == corrected
    effective = FirestoreScoringStore(client).load()
    assert effective.languages == ['Español (nativo)']
    post(data=f'cv:approve:{review}:{first}')
    assert rendered == []
    post(data=f'cv:approve:{review}:{revision}')
    assert rendered == [corrected] and pdfs == [corrected.encode()]
    post(data=f'cv:approve:{review}:{revision}')
    assert rendered == [corrected] and pdfs == [corrected.encode(), corrected.encode()]
    assert len(profiles_seen) == 1
    assert 'sensitive transport detail' not in caplog.text
    post('/build-profile')
    assert len(rebuilds) == 1
    assert FirestoreScoringStore(client).load().languages == ['Español (nativo)']
    assert 'English (B2)' in client.docs['profiles/current']['inferred_profile']['languages']
    post('/ajustar_cv', 92)
    assert previews[-1][2] == corrected
    assert profiles_seen[-1].languages == ['Español (nativo)']
    assert len(profiles_seen) == 2
