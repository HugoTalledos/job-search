import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from job_contracts import CollectorPlan, SearchPreferences
from job_contracts.models import SearchPlan, SearchQuery

from job_agent.application.preference_models import DraftResolution, PreferenceDraft, PreferencesView, Proposal

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
DRAFT_ID = "0123456789abcdef0123456789abcdef"
MIGRATION_HINT = "No hay preferencias guardadas. Ejecuta job_agent seed-search-preferences en tu Mac."
INVALID = "⌛ Esta propuesta ya no es válida; vuelve a escribir /preferencias con lo que quieres."


@pytest.fixture
def chat_http():
    from types import SimpleNamespace

    from job_agent.adapters.notifications.telegram_preferences import TelegramPreferencesChat

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    chat = TelegramPreferencesChat("TOKEN", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))
    return SimpleNamespace(chat=chat, requests=requests, payload=lambda i=-1: json.loads(requests[i].content))


def _plan(n, version=1):
    queries = [SearchQuery(keywords=f"kw{i}", location="Lima" if i % 2 == 0 else None) for i in range(n)]
    return CollectorPlan(search=SearchPlan(queries=queries, posted_within_days=2), max_details_per_run=10,
                         preferences_version=version)


def _draft(diff):
    return PreferenceDraft(draft_id=DRAFT_ID, chat_id="42", base_version=1, preferences=SearchPreferences(),
                           diff=diff, created_at=NOW, expires_at=NOW + timedelta(hours=24))


def test_show_lists_every_field_with_its_label_and_the_plan_size(chat_http):
    prefs = SearchPreferences(keywords_include=["Python", "<b>&"], locations=["Lima"], exclude_companies=["Acme"],
                              work_types=["remote"], version=3)

    chat_http.chat.show(PreferencesView(preferences=prefs, plan=_plan(4, version=3)))

    request = chat_http.requests[0]
    assert request.url.path == "/botTOKEN/sendMessage"
    payload = chat_http.payload()
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"
    text = payload["text"]
    for label in ("Palabras clave", "Palabras clave excluidas", "Usar keywords del perfil", "Ubicaciones",
                  "Días desde la publicación", "Modalidades", "Niveles de experiencia", "Empresas excluidas",
                  "Palabras excluidas del título"):
        assert label in text
    assert "Python, &lt;b&gt;&amp;" in text and "<b>&" not in text
    assert "4 búsquedas en el plan vigente" in text
    assert "⚠️" not in text


def test_show_warns_when_the_plan_comes_from_an_older_version(chat_http):
    chat_http.chat.show(PreferencesView(preferences=SearchPreferences(version=3), plan=_plan(2, version=2)))
    assert "⚠️ El plan aún no refleja la versión actual de tus preferencias" in chat_http.payload()["text"]


def test_show_without_preferences_gives_the_migration_hint(chat_http):
    chat_http.chat.show(PreferencesView(preferences=None, plan=None))
    assert chat_http.payload()["text"] == MIGRATION_HINT


def test_proposal_escapes_html(chat_http):
    proposal = Proposal(kind="draft", draft=_draft(["➕ Palabras clave: <b>&"]), plan_preview=_plan(7),
                        total_queries=9, problems=["No sé qué es <script>"])

    chat_http.chat.proposal(proposal)

    payload = chat_http.payload()
    text = payload["text"]
    assert payload["parse_mode"] == "HTML"
    assert text.startswith("Entendí estos cambios:")
    assert "➕ Palabras clave: &lt;b&gt;&amp;" in text and "<b>&" not in text
    assert "7 búsquedas" in text
    assert "kw0 @ Lima" in text and "kw4 @ Lima" in text and "kw5" not in text
    assert "kw1\n" in text or "kw1 " in text  # a query without location shows only its keywords
    assert "9" in text  # the cut warning mentions how many were possible
    assert "No entendí:" in text and "No sé qué es &lt;script&gt;" in text
    assert text.index("búsquedas") < text.index("No entendí:")
    assert payload["reply_markup"] == {"inline_keyboard": [[
        {"text": "✅ Aplicar", "callback_data": f"pref:apply:{DRAFT_ID}"},
        {"text": "❌ Cancelar", "callback_data": f"pref:cancel:{DRAFT_ID}"},
    ]]}


def test_proposal_without_cut_has_no_cut_warning_and_no_problems_section(chat_http):
    chat_http.chat.proposal(Proposal(kind="draft", draft=_draft(["➕ Palabras clave: Go"]), plan_preview=_plan(2),
                                     total_queries=2))
    text = chat_http.payload()["text"]
    assert "2 búsquedas" in text and "⚠️" not in text and "No entendí" not in text


def test_proposal_rejected_lists_the_problems(chat_http):
    chat_http.chat.proposal(Proposal(kind="rejected", problems=["No hay cambios <x>", "Otro"]))
    payload = chat_http.payload()
    assert payload["text"].startswith("No apliqué cambios:")
    assert "No hay cambios &lt;x&gt;" in payload["text"] and "Otro" in payload["text"]
    assert "reply_markup" not in payload


def test_proposal_without_preferences_gives_the_migration_hint(chat_http):
    chat_http.chat.proposal(Proposal(kind="missing_preferences"))
    assert chat_http.payload()["text"] == MIGRATION_HINT


@pytest.mark.parametrize("resolution, expected", [
    (DraftResolution(status="applied", plan=_plan(6)), "✅ Aplicado: 6 búsquedas"),
    (DraftResolution(status="applied", plan_kept=True),
     "✅ Preferencias guardadas. El plan se mantiene hasta que haya palabras clave."),
    (DraftResolution(status="cancelled"), "❌ Cancelado"),
    (DraftResolution(status="stale", preferences_version=4), INVALID),
    (DraftResolution(status="expired"), INVALID),
    (DraftResolution(status="not_found"), INVALID),
    (DraftResolution(status="already_resolved", previous_status="APPLIED"), "✅ Aplicado"),
    (DraftResolution(status="already_resolved", previous_status="CANCELLED"), "❌ Cancelado"),
    (DraftResolution(status="already_resolved", previous_status="EXPIRED"), INVALID),
])
def test_resolved_edits_the_message_and_removes_the_keyboard(chat_http, resolution, expected):
    chat_http.chat.resolved(77, resolution)

    assert chat_http.requests[0].url.path == "/botTOKEN/editMessageText"
    payload = chat_http.payload()
    assert payload["chat_id"] == "42" and payload["message_id"] == 77
    assert payload["text"] == expected
    assert payload["reply_markup"] == {"inline_keyboard": []}


def test_answer_acknowledges_the_callback(chat_http):
    chat_http.chat.answer("cb-1", "Aplicado")
    assert chat_http.requests[0].url.path == "/botTOKEN/answerCallbackQuery"
    assert chat_http.payload() == {"callback_query_id": "cb-1", "text": "Aplicado"}


def test_long_messages_are_capped_without_breaking_html(chat_http):
    diff = [f"➕ Palabras clave: {'<&>' * 40} {i}" for i in range(200)]
    chat_http.chat.proposal(Proposal(kind="draft", draft=_draft(diff), plan_preview=_plan(3), total_queries=3))
    payload = chat_http.payload()
    text = payload["text"]
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert not re.search(r"&[a-z#0-9]*$", text)  # no entity cut in half
    assert payload["reply_markup"]["inline_keyboard"]


def _make(handler):
    from job_agent.adapters.notifications.telegram_preferences import TelegramPreferencesChat

    return TelegramPreferencesChat("TOKEN", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))


CALLS = [
    lambda c: c.show(PreferencesView(preferences=SearchPreferences(keywords_include=["private"]), plan=None)),
    lambda c: c.proposal(Proposal(kind="rejected", problems=["private"])),
    lambda c: c.resolved(5, DraftResolution(status="cancelled")),
    lambda c: c.answer("cb", "private"),
]


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("status, body", [(500, {"ok": False}), (200, {"ok": False}), (200, []), (400, {})])
def test_failures_raise_without_token_or_body(call, status, body):
    chat = _make(lambda request: httpx.Response(status, json=body, headers={"X": "private"}))
    with pytest.raises(RuntimeError) as error:
        call(chat)
    assert "TOKEN" not in str(error.value) and "private" not in str(error.value)


@pytest.mark.parametrize("call", CALLS)
def test_network_failures_raise_without_token(call):
    def handler(request):
        raise httpx.ConnectError("https://api.telegram.org/botTOKEN private", request=request)

    with pytest.raises(RuntimeError) as error:
        call(_make(handler))
    assert "TOKEN" not in str(error.value) and "private" not in str(error.value)
