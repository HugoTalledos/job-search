import json

import httpx
import pytest


@pytest.fixture
def make_delivery():
    from job_agent.adapters.notifications.telegram_cv import TelegramCvDelivery

    def make(handler):
        return TelegramCvDelivery('TOKEN', client=httpx.Client(transport=httpx.MockTransport(handler)))
    return make


def test_summary_is_plain_text_reply_and_returns_confirmed_message_id(make_delivery):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 72}})
    delivery = make_delivery(handler)
    assert delivery.send_summary('42', 91, 'Python <dev> & Kubernetes') == 72
    assert requests[0].url.path == '/botTOKEN/sendMessage'
    payload = json.loads(requests[0].content)
    assert payload['chat_id'] == '42' and payload['reply_to_message_id'] == 91
    assert payload['text'] == 'Python <dev> & Kubernetes'
    assert 'parse_mode' not in payload  # No entity/tag parsing or truncation hazards.


def test_summary_fits_telegram_utf16_limit_without_splitting_characters(make_delivery):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 72}})
    text = '<b>&amp;🧑' * 800
    make_delivery(handler).send_summary('42', 91, text)
    sent = requests[0]['text']
    assert len(sent.encode('utf-16-le')) // 2 <= 4096
    assert sent.endswith('…')
    assert text.startswith(sent[:-1])
    assert 'parse_mode' not in requests[0]


def test_pdf_is_uploaded_as_reply_with_filename_and_mime_type(make_delivery):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': 73}})
    assert make_delivery(handler).send_pdf('42', 91, b'%PDF-document') == 73
    request = requests[0]
    assert request.url.path == '/botTOKEN/sendDocument'
    body = request.content
    assert b'name="chat_id"\r\n\r\n42' in body
    assert b'name="reply_to_message_id"\r\n\r\n91' in body
    assert b'filename="cv.pdf"' in body and b'application/pdf' in body and b'%PDF-document' in body


@pytest.mark.parametrize('method', ['send_summary', 'send_pdf'])
@pytest.mark.parametrize('status,body', [
    (500, {'ok': False}), (200, {'ok': False}), (200, {'ok': True}),
    (200, {'ok': True, 'result': {'message_id': '72'}}),
    (200, {'ok': True, 'result': {'message_id': True}}),
    (200, {'ok': True, 'result': {'message_id': 0}}),
    (200, {'ok': True, 'result': None}), (200, []),
])
def test_failure_or_unconfirmed_receipt_raises_safe_error(make_delivery, method, status, body):
    delivery = make_delivery(lambda request: httpx.Response(status, json=body))
    with pytest.raises(RuntimeError) as error:
        getattr(delivery, method)('42', 91, 'private CV' if method == 'send_summary' else b'private CV')
    assert 'TOKEN' not in str(error.value) and 'private CV' not in str(error.value)


@pytest.mark.parametrize('method', ['send_summary', 'send_pdf'])
def test_network_failure_raises_safe_error(make_delivery, method):
    def handler(request):
        raise httpx.ConnectError('https://api.telegram.org/botTOKEN private CV', request=request)
    delivery = make_delivery(handler)
    with pytest.raises(RuntimeError) as error:
        getattr(delivery, method)('42', 91, 'private CV' if method == 'send_summary' else b'private CV')
    assert 'TOKEN' not in str(error.value) and 'private CV' not in str(error.value)
