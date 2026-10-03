import httpx
import pytest

from job_agent.adapters.notifications import TelegramProfileReporter, format_profile_message, register_webhook
from job_agent.adapters.notifications.telegram_notifier import TELEGRAM_LIMIT


def test_first_profile_message_shows_strengths_and_escapes_html(profile):
    risky = profile.model_copy(update={"headline": "Backend <dev> & data"})

    text = format_profile_message(risky, [], first_build=True)

    assert "Tu perfil profesional está listo" in text
    assert "Backend &lt;dev&gt; &amp; data" in text
    assert "mid · 4 años" in text and "Backend Engineer" in text and "Python" in text
    assert "primer perfil" in text and "AWS Lambda" in text


def test_rebuilt_profile_message_lists_changes(profile):
    text = format_profile_message(profile, ["Habilidades nuevas: Go (básico)"], first_build=False)

    assert "Novedades frente al perfil anterior" in text and "• Habilidades nuevas: Go (básico)" in text


def test_unchanged_profile_says_so(profile):
    assert "Sin cambios" in format_profile_message(profile, [], first_build=False)


def test_long_message_fits_telegram(profile):
    text = format_profile_message(profile, ["x" * 1000] * 12, first_build=False)

    assert len(text) <= TELEGRAM_LIMIT


def test_reporter_sends_through_telegram(profile):
    class Telegram:
        sent = []

        def send_text(self, text):
            self.sent.append(text)

    telegram = Telegram()
    reporter = TelegramProfileReporter(telegram)
    reporter.built(profile, [], first_build=True)
    reporter.failed()

    assert "listo" in telegram.sent[0] and "No pude" in telegram.sent[1]


def test_register_webhook_sets_secret_and_commands():
    calls = []

    def handler(request):
        calls.append((request.url.path, request.read()))
        return httpx.Response(200, json={"ok": True})

    register_webhook("TOKEN", "https://agent.example/webhooks/telegram", "s3cret",
                     client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert [path for path, _ in calls] == ["/botTOKEN/setWebhook", "/botTOKEN/setMyCommands"]
    assert b'"secret_token":"s3cret"' in calls[0][1]
    assert b"build_profile" in calls[1][1] and b"resend_pending" in calls[1][1]


def test_register_webhook_fails_loudly():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"ok": False})))

    with pytest.raises(RuntimeError, match="setWebhook"):
        register_webhook("TOKEN", "https://x", "s", client=client)
