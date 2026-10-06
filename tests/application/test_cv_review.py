from types import SimpleNamespace
import pytest
from tests.application.test_generate_tailored_cv import harness, NOW
from job_agent.domain.cv_corrections import CorrectionSet, FactOperation
from job_agent.adapters.persistence.firestore_cv_reviews import FirestoreCvReviewStore
from job_agent.adapters.persistence.firebase_cv_artifacts import FirebaseCvArtifactStore

@pytest.fixture
def review_harness(harness):
    from job_agent.application.cv_review import CvReviewService
    h = harness
    h.corrections = CorrectionSet(version=0, operations=[])
    h.reviews = FirestoreCvReviewStore(h.client)
    h.use_case.artifacts = FirebaseCvArtifactStore('private-bucket', h.storage_client)
    h.service = CvReviewService(generation=h.use_case, reviews=h.reviews,
        corrections=SimpleNamespace(load=lambda: h.corrections))
    return h

def test_draft_no_pdf_and_resume_without_tailoring(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    assert not {'render', 'send_pdf'}.intersection(h.events)
    assert h.service.generate_draft(review.review_id, '42') == revision
    assert h.events.count('tailor') == 1

def test_approval_renders_exact_active_markdown_and_preserves_offer(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    first = h.service.generate_draft(review.review_id, '42')
    uri = h.use_case.artifacts.save_markdown(review.review_id, 'abcdefghijklmnop', '# Exact edited CV')
    second = h.reviews.publish_revision(review.review_id, first.revision_id, uri, 'editorial')
    with pytest.raises(ValueError, match='stale'):
        h.service.approve(review.review_id, first.revision_id, '42')
    result = h.service.approve(review.review_id, second.revision_id, '42')
    assert h.calls['render'][0][0] == ('# Exact edited CV',)
    assert h.calls['send_pdf'][0][0][-1] == 'offer'
    assert result.delivery_status == 'SENT'
    h.service.approve(review.review_id, second.revision_id, '42')
    assert h.events.count('render') == h.events.count('send_pdf') == 1

def test_denial_changes_key_and_rejects_contradictions(review_harness):
    h = review_harness
    first = h.service.prepare('offer', '42', NOW)
    h.corrections = CorrectionSet(version=1, operations=[FactOperation('deny_claim', 'English')])
    second = h.service.prepare('offer', '42', NOW)
    assert second.key.version_id != first.key.version_id
    h.state.tailored = h.state.tailored.model_copy(update={'resume_markdown': 'English B2'})
    with pytest.raises(ValueError, match='contradict'):
        h.service.generate_draft(second.review_id, '42')
    assert h.reviews.load(second.review_id, '42').active_revision_id is None

def test_renderer_failure_leaves_approved_and_retry_does_not_tailor(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    h.state.fail = 'render'
    with pytest.raises(RuntimeError):
        h.service.approve(review.review_id, revision.revision_id, '42')
    assert h.reviews.load(review.review_id, '42').status == 'APPROVED'
    h.state.fail = None
    assert h.service.approve(review.review_id, revision.revision_id, '42').delivery_status == 'SENT'
    assert h.events.count('tailor') == 1

def test_upload_failure_never_publishes_revision(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    h.storage_client.fail_on = 'resume.md'
    with pytest.raises(RuntimeError):
        h.service.generate_draft(review.review_id, '42')
    assert h.reviews.load(review.review_id, '42').active_revision_id is None


def test_failed_delivery_retries_only_missing_receipt(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    h.state.fail = 'send_pdf'
    with pytest.raises(RuntimeError):
        h.service.approve(review.review_id, revision.revision_id, '42', reply_to_message_id=91)
    h.state.fail = None
    assert h.service.approve(review.review_id, revision.revision_id, '42').delivery_status == 'SENT'
    assert h.events.count('render') == h.events.count('tailor') == h.events.count('send_summary') == 1


def test_approval_rechecks_current_corrections_before_approving(review_harness):
    h = review_harness
    h.state.tailored = h.state.tailored.model_copy(update={'resume_markdown': 'English B2'})
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    h.corrections = CorrectionSet(version=1, operations=[FactOperation('remove_language', 'English')])
    with pytest.raises(ValueError, match='contradict'):
        h.service.approve(review.review_id, revision.revision_id, '42')
    assert h.reviews.load(review.review_id, '42').status == 'DRAFT'
    assert 'render' not in h.events


def test_new_noncontradictory_correction_cannot_reuse_stale_ready_analysis(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    first = h.service.approve(review.review_id, revision.revision_id, '42')
    h.corrections = CorrectionSet(version=1, operations=[FactOperation('deny_claim', 'managed international teams')])
    h.state.match = h.state.match.model_copy(update={'reasons': ['Updated confirmed facts']})
    second = h.service.approve(review.review_id, revision.revision_id, '42')
    assert second.key.corrections_version == 1
    assert second.key.version_id != first.key.version_id
    assert h.store.load_ready(second.key).match.reasons == ['Updated confirmed facts']
    assert h.store.load_ready(first.key).match.reasons != ['Updated confirmed facts']
    assert h.events.count('tailor') == 1
