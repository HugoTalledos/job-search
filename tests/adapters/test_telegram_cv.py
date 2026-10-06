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
    assert make_delivery(handler).send_pdf('42', 91, b'%PDF-document', 'offer') == 73
    request = requests[0]
    assert request.url.path == '/botTOKEN/sendDocument'
    body = request.content
    assert b'name="chat_id"\r\n\r\n42' in body
    assert b'name="reply_to_message_id"\r\n\r\n91' in body
    assert b'filename="cv.pdf"' in body and b'application/pdf' in body and b'%PDF-document' in body
    assert b'name="reply_markup"' in body
    assert b'applied:offer' in body
    assert '✅ Apliqué'.encode() in body


@pytest.mark.parametrize('posting_id', ['', '../offer', 'a' * 49])
def test_pdf_rejects_invalid_button_posting_id_before_sending(make_delivery, posting_id):
    requests = []
    delivery = make_delivery(lambda request: requests.append(request) or httpx.Response(200))

    with pytest.raises(ValueError, match='posting id'):
        delivery.send_pdf('42', 91, b'%PDF-document', posting_id)

    assert requests == []


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
        args = ('42', 91, 'private CV') if method == 'send_summary' else ('42', 91, b'private CV', 'offer')
        getattr(delivery, method)(*args)
    assert 'TOKEN' not in str(error.value) and 'private CV' not in str(error.value)


@pytest.mark.parametrize('method', ['send_summary', 'send_pdf'])
def test_network_failure_raises_safe_error(make_delivery, method):
    def handler(request):
        raise httpx.ConnectError('https://api.telegram.org/botTOKEN private CV', request=request)
    delivery = make_delivery(handler)
    with pytest.raises(RuntimeError) as error:
        args = ('42', 91, 'private CV') if method == 'send_summary' else ('42', 91, b'private CV', 'offer')
        getattr(delivery, method)(*args)
    assert 'TOKEN' not in str(error.value) and 'private CV' not in str(error.value)


def test_preview_uploads_full_markdown_then_buttons_with_bounded_callbacks(make_delivery):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(requests) + 70}})
    delivery = make_delivery(handler)
    markdown = '# CV\n' + 'Experiencia 🧑\n' * 2000
    receipt = delivery.send_preview('42', 91, 'r' * 16, 'v' * 16, 'Vista previa', markdown)
    assert receipt.markdown_message_id == 71 and receipt.preview_message_id == 72
    assert markdown.encode() in requests[0].content
    payload = json.loads(requests[1].content)
    callbacks = [b['callback_data'] for row in payload['reply_markup']['inline_keyboard'] for b in row]
    assert callbacks == ['cv:approve:' + 'r'*16 + ':' + 'v'*16, 'cv:edit:' + 'r'*16, 'cv:cancel:' + 'r'*16]
    assert all(len(c.encode()) <= 64 for c in callbacks)
    assert 'completo' in payload['text']


def test_edit_proposal_long_diff_is_complete_document_and_states_scope(make_delivery):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'ok': True, 'result': {'message_id': len(requests)}})
    delivery = make_delivery(handler)
    before, after = 'Antes ' * 1000, 'Después ' * 1000
    assert delivery.send_edit_proposal('42', 91, 'r'*16, 'p'*16, before, after, 'global') == 2
    assert before.encode() in requests[0].content and after.encode() in requests[0].content
    payload = json.loads(requests[1].content)
    assert 'futuros CV' in payload['text']
    assert len(payload['text'].encode('utf-16-le')) // 2 <= 4096
    assert payload['reply_markup']['inline_keyboard'][0][0]['callback_data'] == 'cv:confirm:' + 'r'*16 + ':' + 'p'*16


def test_review_pdf_keeps_applied_and_adds_correction(make_delivery):
    requests = []
    delivery = make_delivery(lambda req: requests.append(req) or httpx.Response(200, json={'ok':True,'result':{'message_id':7}}))
    delivery.send_pdf('42', 91, b'%PDF', 'offer', review_id='r'*16)
    assert b'applied:offer' in requests[0].content
    assert ('cv:correct:' + 'r'*16).encode() in requests[0].content


@pytest.mark.parametrize('review_id', ['', 'x'*17, '../review'])
def test_invalid_preview_ids_do_not_send(make_delivery, review_id):
    requests=[]
    delivery=make_delivery(lambda req: requests.append(req))
    with pytest.raises(ValueError): delivery.send_preview('42', 91, review_id, 'v'*16, 'summary', '# CV')
    assert requests == []
