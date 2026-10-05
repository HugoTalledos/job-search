from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore
from job_agent.adapters.persistence.firestore_cv_tracking import FirestoreCvTrackingStore
from job_agent.domain.models import JobRequirement
from tests.adapters.test_firebase_cv_artifacts import Client as StorageClient
from tests.adapters.test_firestore_cv_tracking import Client, version

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


@pytest.fixture
def harness(job, profile, match, tailored):
    from job_agent.application.generate_tailored_cv import GenerateTailoredCv

    events, calls = [], {}
    state = SimpleNamespace(resume='Original CV: Python at Acme, 2020–2024', profile=profile, job=job,
                            fail=None, match=match, tailored=tailored, pdf=b'%PDF-valid')
    state.match = match.model_copy(update={'requirements': [
        JobRequirement(name='Python', priority='must', covered=True, evidence='repo x'),
        JobRequirement(name='Kubernetes', priority='nice', covered=False, evidence='Sin evidencia'),
    ]})
    client, storage_client = Client(), StorageClient()
    store = FirestoreCvTrackingStore(client)
    artifacts = FirebaseCvArtifactStore('private-bucket', storage_client)

    def operation(name, fn):
        def run(*args, **kwargs):
            events.append(name)
            calls.setdefault(name, []).append((args, kwargs))
            if state.fail == name:
                raise RuntimeError(name + ' failed')
            return fn(*args, **kwargs)
        return run

    tracking = SimpleNamespace(**{name: operation(name, getattr(store, name)) for name in (
        'claim', 'mark_ready', 'mark_failed', 'load_ready', 'begin_delivery',
        'mark_summary_sent', 'mark_pdf_sent', 'mark_delivery_failed',
    )})
    use_case = GenerateTailoredCv(
        resume=SimpleNamespace(read=operation('resume', lambda: state.resume)),
        profile_reader=SimpleNamespace(load=operation('profile', lambda: state.profile)),
        posting_reader=SimpleNamespace(load=operation('posting:offer', lambda posting_id: state.job)),
        matcher=SimpleNamespace(score=operation('match', lambda *args: state.match)),
        tailor=SimpleNamespace(tailor=operation('tailor', lambda *args: state.tailored)),
        renderer=SimpleNamespace(render=operation('render', lambda markdown: state.pdf)),
        tracking=tracking,
        artifacts=SimpleNamespace(save=operation('upload', artifacts.save),
                                  read_pdf=operation('read_pdf', artifacts.read_pdf)),
        delivery=SimpleNamespace(send_summary=operation('send_summary', lambda *args: 101),
                                 send_pdf=operation('send_pdf', lambda *args: 102)),
    )
    return SimpleNamespace(use_case=use_case, events=events, calls=calls, state=state, client=client,
                           storage_client=storage_client, store=store)


def test_prepare_reads_sources_in_order_and_does_no_generation(harness):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    assert h.events == ['resume', 'profile', 'posting:offer', 'claim']
    assert request.action == 'generate' and request.attempt_id
    assert request.requested_at == NOW
    assert request.resume_text == h.state.resume
    assert request.profile == h.state.profile and request.posting == h.state.job
    assert h.calls['claim'][0][0] == (request.key, NOW)
    assert h.use_case.prepare('offer', NOW).action == 'in_progress'


@pytest.mark.parametrize('source', ['resume', 'profile', 'posting:offer'])
def test_missing_source_propagates_before_claim_or_render(harness, source):
    h = harness
    h.state.fail = source
    with pytest.raises(RuntimeError):
        h.use_case.prepare('offer', NOW)
    assert 'claim' not in h.events and 'render' not in h.events
    assert h.client.docs == {}


@pytest.mark.parametrize('missing', ['resume', 'profile', 'job', 'description'])
def test_empty_required_input_rejected_before_claim(harness, missing):
    h = harness
    if missing == 'description':
        h.state.job = h.state.job.model_copy(update={'description': ' \n '})
    else:
        setattr(h.state, missing, ' ' if missing == 'resume' else None)
    with pytest.raises((ValueError, LookupError)):
        h.use_case.prepare('offer', NOW)
    assert 'claim' not in h.events and 'render' not in h.events


@pytest.mark.parametrize('changed', ['resume', 'profile', 'job'])
def test_input_change_claims_new_version(harness, changed):
    h = harness
    first = h.use_case.prepare('offer', NOW)
    if changed == 'resume':
        h.state.resume += '\nNew role'
    elif changed == 'profile':
        h.state.profile = h.state.profile.model_copy(update={'summary': 'Additional evidence'})
    else:
        h.state.job = h.state.job.model_copy(update={'description': 'New complete description'})
    second = h.use_case.prepare('offer', NOW)
    assert second.key.version_id != first.key.version_id and second.action == 'generate'


def test_generation_receives_complete_inputs_uploads_then_publishes_then_delivers(harness):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    result = h.use_case.execute(request, '42', 91)
    assert (result.generation_status, result.delivery_status) == ('READY', 'SENT')
    assert h.calls['match'][0][0] == (h.state.job, h.state.profile, h.state.resume)
    assert h.calls['tailor'][0][0] == (h.state.job, h.state.match, h.state.profile, h.state.resume)
    assert h.calls['render'][0][0] == (h.state.tailored.resume_markdown,)
    assert h.events.index('upload') < h.events.index('mark_ready') < h.events.index('send_summary')
    assert h.events.index('mark_summary_sent') < h.events.index('send_pdf') < h.events.index('mark_pdf_sent')
    assert h.calls['upload'][0][1] == {'attempt_id': request.attempt_id}
    assert h.calls['mark_ready'][0][1] == {'attempt_id': request.attempt_id}
    ready = h.store.load_ready(request.key)
    assert ready.summary_message_id == 101 and ready.pdf_message_id == 102
    assert h.client.docs['application_tracking/offer']['stage'] == 'CV_READY'
    assert h.calls['send_pdf'][0][0] == ('42', 91, h.state.pdf, 'offer')
    summary = h.calls['send_summary'][0][0][2]
    assert h.state.job.title in summary and h.state.job.company in summary
    assert 'Python avanzado' in summary and 'Kubernetes' in summary and 'Brechas' in summary
    assert 'Kubernetes' not in h.calls['render'][0][0][0]
    assert not ready.match.requirements[1].covered


def test_readme_contains_evidence_reasons_gaps_changes_and_input_fingerprints(harness):
    from job_agent.application.generate_tailored_cv import build_cv_readme
    h = harness
    request = h.use_case.prepare('offer', NOW)
    readme = build_cv_readme(h.state.job, h.state.match, h.state.tailored, request.key, NOW)
    for text in (h.state.job.title, h.state.job.company, h.state.job.url, 'Python', 'repo x',
                 'Kubernetes', 'Sin evidencia', 'Python avanzado', 'Enfocado a backend',
                 'La oferta pide Python', request.key.resume_fingerprint, request.key.profile_fingerprint,
                 request.key.job_fingerprint, request.key.version_id, '2026-10-03T12:00:00+00:00'):
        assert text in readme
    assert readme == build_cv_readme(h.state.job, h.state.match, h.state.tailored, request.key, NOW)
    h.use_case.execute(request, '42', 91)
    assert h.calls['upload'][0][0][3] == readme


def test_low_score_still_generates_requested_cv(harness):
    h = harness
    h.state.match = h.state.match.model_copy(update={'score': 1, 'verdict': 'no'})
    result = h.use_case.execute(h.use_case.prepare('offer', NOW), '42', 91)
    assert result.generation_status == 'READY' and 'tailor' in h.events


def test_in_progress_execution_sends_nothing(harness):
    h = harness
    h.use_case.prepare('offer', NOW)
    request = h.use_case.prepare('offer', NOW)
    h.events.clear()
    result = h.use_case.execute(request, '42', 91)
    assert (result.generation_status, result.delivery_status) == ('PROCESSING', 'PENDING')
    assert h.events == []


@pytest.mark.parametrize('failure', ['match', 'tailor', 'render', 'upload', 'mark_ready'])
def test_generation_failure_marks_failed_without_publishing_or_sending(harness, failure):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    h.state.fail = failure
    with pytest.raises(RuntimeError):
        h.use_case.execute(request, '42', 91)
    assert version(h.client, request.key)['generation_status'] == 'FAILED'
    assert h.client.docs['application_tracking/offer'].get('stage') != 'CV_READY'
    assert h.calls['mark_failed'][0][1] == {'attempt_id': request.attempt_id}
    assert 'send_summary' not in h.events and 'send_pdf' not in h.events


def test_partial_storage_upload_marks_failed_and_never_publishes_ready(harness):
    h = harness
    h.storage_client.fail_on = 'README.md'
    request = h.use_case.prepare('offer', NOW)
    with pytest.raises(RuntimeError):
        h.use_case.execute(request, '42', 91)
    assert h.storage_client.objects  # Some private objects exist, no published metadata.
    assert version(h.client, request.key)['generation_status'] == 'FAILED'
    assert h.client.docs['application_tracking/offer'].get('stage') != 'CV_READY'
    assert 'mark_ready' not in h.events and 'send_pdf' not in h.events


def test_empty_pdf_fails_before_upload(harness):
    h = harness
    h.state.pdf = b''
    request = h.use_case.prepare('offer', NOW)
    with pytest.raises(ValueError):
        h.use_case.execute(request, '42', 91)
    assert 'upload' not in h.events
    assert version(h.client, request.key)['generation_status'] == 'FAILED'


@pytest.mark.parametrize('failure', ['send_summary', 'mark_summary_sent', 'send_pdf', 'mark_pdf_sent', 'read_pdf'])
def test_delivery_failure_keeps_generation_ready_and_records_failure(harness, failure):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    h.state.fail = failure
    with pytest.raises(RuntimeError):
        h.use_case.execute(request, '42', 91)
    ready = h.store.load_ready(request.key)
    assert ready.delivery_status == 'FAILED'
    assert version(h.client, request.key)['generation_status'] == 'READY'
    assert h.events[-1] == 'mark_delivery_failed'
    assert 'mark_failed' not in h.events
    if failure in ('send_summary', 'mark_summary_sent'):
        assert 'send_pdf' not in h.events


def test_pdf_failure_retry_reads_ready_artifacts_and_sends_only_pdf(harness):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    h.state.fail = 'send_pdf'
    with pytest.raises(RuntimeError):
        h.use_case.execute(request, '42', 91)
    assert h.store.load_ready(request.key).summary_message_id == 101
    retry = h.use_case.prepare('offer', NOW)
    h.events.clear()
    # Another failed retry must still persist delivery failure after reading the PDF.
    with pytest.raises(RuntimeError):
        h.use_case.execute(retry, '42', 91)
    assert h.events == ['load_ready', 'read_pdf', 'send_pdf', 'mark_delivery_failed']
    h.state.fail = None
    h.events.clear()
    result = h.use_case.execute(retry, '42', 91)
    assert h.events == ['load_ready', 'read_pdf', 'send_pdf', 'mark_pdf_sent']
    assert result.delivery_status == 'SENT'


def test_explicit_request_resends_both_and_failed_resend_retries_missing_pdf(harness):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    h.use_case.execute(request, '42', 91)
    retry = h.use_case.prepare('offer', NOW)
    h.events.clear()
    h.state.fail = 'send_pdf'
    with pytest.raises(RuntimeError):
        h.use_case.execute(retry, '42', 92)
    assert 'begin_delivery' in h.events and 'send_summary' in h.events
    assert not {'match', 'tailor', 'render', 'upload'}.intersection(h.events)
    ready = h.store.load_ready(request.key)
    assert ready.summary_message_id == 101 and ready.pdf_message_id is None
    h.state.fail = None
    h.events.clear()
    h.use_case.execute(h.use_case.prepare('offer', NOW), '42', 93)
    assert 'send_summary' not in h.events and 'send_pdf' in h.events
    assert h.store.load_ready(request.key).delivery_status == 'SENT'


def test_prepare_rejects_ambiguous_naive_request_time_before_claim(harness):
    with pytest.raises(ValueError, match='timezone'):
        harness.use_case.prepare('offer', NOW.replace(tzinfo=None))
    assert 'claim' not in harness.events


def test_confirmed_receipts_reconcile_failed_delivery_without_sending_again(harness):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    h.use_case.execute(request, '42', 91)
    # A final receipt committed but the caller observed an error and recorded FAILED.
    h.store.mark_delivery_failed(request.key)
    h.events.clear()
    result = h.use_case.execute(h.use_case.prepare('offer', NOW), '42', 91)
    assert not {'send_summary', 'send_pdf', 'read_pdf'}.intersection(h.events)
    assert result.delivery_status == h.store.load_ready(request.key).delivery_status == 'SENT'


def test_failed_generation_reclaims_new_attempt_and_finishes_same_version(harness):
    h = harness
    request = h.use_case.prepare('offer', NOW)
    h.state.fail = 'render'
    with pytest.raises(RuntimeError):
        h.use_case.execute(request, '42', 91)
    h.state.fail = None
    retry = h.use_case.prepare('offer', NOW)
    assert retry.key == request.key and retry.attempt_id != request.attempt_id
    h.use_case.execute(retry, '42', 91)
    assert h.store.load_ready(request.key).delivery_status == 'SENT'


def test_uncovered_requirement_stays_in_report_and_summary_even_without_legacy_gap(harness):
    h = harness
    h.state.match = h.state.match.model_copy(update={'gaps': []})
    h.use_case.execute(h.use_case.prepare('offer', NOW), '42', 91)
    assert 'Kubernetes: Sin evidencia' in h.calls['send_summary'][0][0][2]
    assert 'Kubernetes (deseable; brecha): Sin evidencia' in h.calls['upload'][0][0][3]
    assert 'Kubernetes' not in h.calls['render'][0][0][0]
