import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from job_agent.entrypoints.http import create_app
from job_agent.scoring.run import ScoreReport


class Runner:
    def __init__(self, result=None, error=None):
        self.result = [] if result is None else result
        self.error = error
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class Messenger:
    def __init__(self):
        self.messages = []

    def send_text(self, text):
        self.messages.append(text)


def test_inference_reports_start_and_empty_finish_to_telegram():
    messenger = Messenger()
    response = TestClient(create_app(Runner(ScoreReport()), "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 200
    assert messenger.messages == [
        "Inicié la evaluación de ofertas de esta corrida.",
        "Evaluación terminada: no había ofertas pendientes y no se enviaron propuestas.",
    ]


def test_inference_reports_evaluated_but_unnotified_offers():
    messenger = Messenger()
    TestClient(create_app(Runner(ScoreReport(evaluated=3, below_threshold=3, min_score_to_notify=70)), "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert messenger.messages[-1] == "Evaluación terminada: 3 ofertas evaluadas, ninguna se notificó. Motivos: 3 no alcanzaron el puntaje mínimo de afinidad con tu perfil (70/100)."


def test_inference_explains_previously_notified_and_excluded_offers():
    messenger = Messenger()
    TestClient(create_app(Runner(ScoreReport()), "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"},
        json={"collection": {"known_notified": 2, "excluded": 1, "known_other": 0}}
    )
    assert messenger.messages[-1] == (
        "Evaluación terminada: 0 ofertas evaluadas, ninguna se notificó. "
        "Motivos: 2 ya se habían notificado, 1 se excluyó por tus preferencias."
    )


def test_inference_does_not_label_unknown_known_offers_as_notified():
    messenger = Messenger()
    TestClient(create_app(Runner(ScoreReport()), "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"},
        json={"collection": {"known_notified": 0, "excluded": 0, "known_other": 2}}
    )
    assert "2 ya estaban registradas" in messenger.messages[-1]
    assert "ya se habían notificado" not in messenger.messages[-1]


def test_inference_explains_single_offer_below_threshold_and_failed_send():
    messenger = Messenger()
    TestClient(create_app(Runner(ScoreReport(evaluated=2, failed=1, below_threshold=1,
                                             min_score_to_notify=80)), "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )
    assert messenger.messages[-1] == (
        "Evaluación terminada: 2 ofertas evaluadas, ninguna se notificó, 1 fallida. "
        "Motivos: 1 no alcanzó el puntaje mínimo de afinidad con tu perfil (80/100), "
        "1 falló durante la evaluación o el envío."
    )


def test_inference_reports_notified_and_failed_counts():
    messenger = Messenger()
    TestClient(create_app(Runner(ScoreReport(evaluated=1, notified=1, failed=2)),
                          "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert messenger.messages[-1] == "Evaluación terminada: 1 oferta evaluada, 1 notificada, 2 fallidas."


def test_inference_reports_background_failure_to_telegram():
    messenger = Messenger()
    TestClient(create_app(Runner(error=RuntimeError("private details")), "secret-key", messenger=messenger)).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert messenger.messages[-1] == "No pude completar la evaluación de esta corrida. Revisa los registros del servicio."
    assert "private details" not in " ".join(messenger.messages)


def test_collector_start_and_failure_are_reported_to_telegram():
    messenger = Messenger()
    client = TestClient(create_app(Runner(), "secret-key", messenger=messenger))

    assert client.post("/webhooks/collection-status", headers={"X-API-Key": "secret-key"},
                       json={"status": "started"}).status_code == 200
    assert client.post("/webhooks/collection-status", headers={"X-API-Key": "secret-key"},
                       json={"status": "issues"}).status_code == 200
    assert client.post("/webhooks/collection-status", headers={"X-API-Key": "secret-key"},
                       json={"status": "failed"}).status_code == 200
    assert messenger.messages == [
        "Inicié una nueva búsqueda de ofertas.",
        "La búsqueda terminó con errores; algunos resultados pueden faltar. Revisa los registros del buscador.",
        "No pude completar la búsqueda de esta corrida. Revisa los registros del buscador.",
    ]


def test_collection_status_rejects_unauthorized_and_unknown_values():
    messenger = Messenger()
    client = TestClient(create_app(Runner(), "secret-key", messenger=messenger))

    assert client.post("/webhooks/collection-status", json={"status": "started"}).status_code == 401
    assert client.post("/webhooks/collection-status", headers={"X-API-Key": "secret-key"},
                       json={"status": "unknown"}).status_code == 422
    assert messenger.messages == []


def test_webhook_returns_empty_200_after_scheduling():
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).post(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 200
    assert response.content == b""
    assert runner.calls == 1


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_webhook_rejects_unauthorized_without_work(headers):
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).post("/webhooks/inference", headers=headers)

    assert response.status_code == 401
    assert runner.calls == 0


def test_get_does_not_start_work():
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).get(
        "/webhooks/inference", headers={"X-API-Key": "secret-key"}
    )

    assert response.status_code == 405
    assert runner.calls == 0


def test_background_failure_keeps_200_and_logs_no_sensitive_details(caplog):
    runner = Runner(error=RuntimeError("SECRET-JOB-DESCRIPTION"))

    with caplog.at_level(logging.ERROR):
        response = TestClient(create_app(runner, "secret-key")).post(
            "/webhooks/inference", headers={"X-API-Key": "secret-key"}
        )

    assert response.status_code == 200
    assert response.content == b""
    assert b"SECRET-JOB-DESCRIPTION" not in response.content
    assert b"secret-key" not in response.content
    assert "SECRET-JOB-DESCRIPTION" not in caplog.text
    assert "secret-key" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_service_rejects_blank_configured_api_key():
    with pytest.raises(ValueError, match="JOB_AGENT_WEBHOOK_API_KEY"):
        create_app(Runner(), " ")


def test_non_ascii_header_is_unauthorized_without_work():
    runner = Runner()

    response = TestClient(create_app(runner, "secret-key")).post(
        "/webhooks/inference", headers={b"X-API-Key": b"\xff"}
    )

    assert response.status_code == 401
    assert runner.calls == 0


def test_utf8_api_key_authenticates():
    runner = Runner()

    response = TestClient(create_app(runner, "contraseña")).post(
        "/webhooks/inference", headers={b"X-API-Key": "contraseña".encode("utf-8")}
    )

    assert response.status_code == 200
    assert runner.calls == 1


def test_response_body_is_sent_before_background_scoring_finishes():
    release = threading.Event()

    class BlockingRunner:
        finished = False

        def execute(self):
            release.wait(timeout=3)
            self.finished = True

    runner = BlockingRunner()
    app = create_app(runner, "secret-key")
    body_sent = asyncio.Event()

    async def run_request():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                body_sent.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "method": "POST",
            "path": "/webhooks/inference", "raw_path": b"/webhooks/inference",
            "root_path": "", "query_string": b"", "headers": [(b"x-api-key", b"secret-key")],
            "http_version": "1.1", "scheme": "http", "server": ("test", 80), "client": ("test", 123),
        }
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(body_sent.wait(), timeout=2)
            assert runner.finished is False
        finally:
            release.set()
        await asyncio.wait_for(task, timeout=2)
        assert runner.finished is True

    asyncio.run(run_request())


def test_background_runs_are_serialized_within_one_app():
    first_started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()

    class BlockingRunner:
        calls = 0

        def execute(self):
            self.calls += 1
            if self.calls == 1:
                first_started.set()
                release.wait(timeout=3)
            else:
                second_started.set()

    runner = BlockingRunner()
    app = create_app(runner, "secret-key")

    def post():
        return TestClient(app).post("/webhooks/inference", headers={"X-API-Key": "secret-key"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(post)
        assert first_started.wait(timeout=2)
        second = pool.submit(post)
        try:
            assert not second_started.wait(timeout=0.2)
        finally:
            release.set()
        assert first.result(timeout=2).status_code == 200
        assert second.result(timeout=2).status_code == 200
    assert runner.calls == 2
