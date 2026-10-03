from job_agent.adapters.notifications import TelegramNotifier


def test_telegram_notifier_sends_html_text(monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        text = ""

    def fake_post(url, data=None, timeout=None):
        calls.append((url, data))
        return Resp()

    monkeypatch.setattr("job_agent.adapters.notifications.telegram_notifier.httpx.post", fake_post)
    TelegramNotifier("TOKEN", "42").send_text("hola")
    [(url, data)] = calls
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert data["chat_id"] == "42" and data["text"] == "hola" and data["parse_mode"] == "HTML"
