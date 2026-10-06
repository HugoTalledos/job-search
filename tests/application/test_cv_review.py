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


def editing(h, markdown, replacements, facts=()):
    from job_agent.application.cv_review_models import CvEditProposal, TextReplacement
    from job_agent.adapters.persistence.firestore_profile_corrections import FirestoreProfileCorrections
    h.client.collection('profiles').document('current').set(h.state.profile.model_dump())
    h.service.corrections = FirestoreProfileCorrections(h.client)
    h.service.interpreter = SimpleNamespace(propose=lambda *a: CvEditProposal(
        replacements=[TextReplacement(old_text=a, new_text=b) for a,b in replacements],
        fact_operations=list(facts), explanation='Cambio puntual'))
    h.state.tailored = h.state.tailored.model_copy(update={'resume_markdown': markdown})
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    return review, revision


def test_confirm_multiple_exact_style_replacements(review_harness):
    h = review_harness
    review, first = editing(h, '# CV\nOld headline\nOld skill', [('Old headline','New headline'), ('Old skill','New skill')])
    proposal = h.service.propose_edit(review.review_id, '42', 'mejora redacción')
    second = h.service.confirm_edit(review.review_id, proposal.proposal_id, '42')
    assert h.use_case.artifacts.read_markdown(second.markdown_uri) == '# CV\nNew headline\nNew skill'
    assert h.service.corrections.load().version == 0
    with pytest.raises(ValueError):
        h.service.confirm_edit(review.review_id, proposal.proposal_id, '42')
    assert h.events.count('tailor') == 1


@pytest.mark.parametrize('markdown,replacements', [('# CV\nEnglish B2',[('missing','')]),
    ('# CV\nEnglish B2\nEnglish B2',[('English B2','')]), ('# CV\nEnglish B2',[('# CV\nEnglish B2','# Replacement')]),
    ('# CV\nEnglish B2',[('English B2',''), ('B2','')])])
def test_ambiguous_or_whole_document_edits_rejected(review_harness, markdown, replacements):
    h = review_harness
    review, first = editing(h, markdown, replacements)
    with pytest.raises(ValueError):
        h.service.propose_edit(review.review_id, '42', 'corregir')
    assert h.reviews.load(review.review_id,'42').active_revision_id == first.revision_id


def test_facts_atomic_with_revision_and_survive_cancel(review_harness):
    h = review_harness
    review, first = editing(h, '# CV\nEnglish B2\nPython', [('English B2\n','')], [FactOperation('remove_language','English')])
    notices=[]
    h.service.after_profile_change = lambda notify: notify('Recompilación pendiente')
    h.service.notify = notices.append
    proposal = h.service.propose_edit(review.review_id,'42','No hablo inglés')
    revision = h.service.confirm_edit(review.review_id,proposal.proposal_id,'42')
    assert h.use_case.artifacts.read_markdown(revision.markdown_uri) == '# CV\nPython'
    assert h.service.corrections.load().version == 1
    h.reviews.cancel(review.review_id, '42')
    assert h.service.corrections.load().operations == [FactOperation('remove_language','English')]
    assert notices == ['Recompilación pendiente']


def test_rejected_and_stale_proposals_do_not_confirm_facts(review_harness):
    h=review_harness
    review, first=editing(h,'# CV\nEnglish B2\nPython',[('English B2\n','')],[FactOperation('remove_language','English')])
    proposal=h.service.propose_edit(review.review_id,'42','No inglés')
    h.service.reject_edit(review.review_id,proposal.proposal_id,'42')
    with pytest.raises(ValueError): h.service.confirm_edit(review.review_id,proposal.proposal_id,'42')
    proposal=h.service.propose_edit(review.review_id,'42','No inglés')
    uri=h.use_case.artifacts.save_markdown(review.review_id,'anotherrevisionx','# Changed')
    h.reviews.publish_revision(review.review_id,first.revision_id,uri,None)
    with pytest.raises(ValueError): h.service.confirm_edit(review.review_id,proposal.proposal_id,'42')
    assert h.service.corrections.load().version == 0


@pytest.mark.parametrize('failure', ['facts', 'revision', 'resolution', 'upload'])
def test_confirmation_failure_rolls_back_all_metadata(review_harness, monkeypatch, failure):
    h=review_harness
    review, first=editing(h,'# CV\nEnglish B2\nPython',[('English B2\n','')],[FactOperation('remove_language','English')])
    proposal=h.service.propose_edit(review.review_id,'42','No inglés')
    if failure == 'upload':
        h.storage_client.fail_on='resume.md'
    else:
        obj, method = {'facts':(h.service.corrections,'apply_in_transaction'),
            'revision':(h.reviews,'publish_revision_in_transaction'),
            'resolution':(h.reviews,'resolve_proposal_in_transaction')}[failure]
        original=getattr(obj,method)
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('simulated midtransaction failure')
        monkeypatch.setattr(obj,method,fail)
    with pytest.raises(RuntimeError): h.service.confirm_edit(review.review_id,proposal.proposal_id,'42')
    assert h.reviews.load(review.review_id,'42').active_revision_id == first.revision_id
    assert h.service.corrections.load().version == 0
    assert h.reviews.load_proposal(review.review_id,proposal.proposal_id).status == 'pending'


def test_correction_version_conflict_and_failed_rebuild(review_harness):
    h=review_harness
    review, first=editing(h,'# CV\nEnglish B2\nPython',[('English B2\n','')],[FactOperation('remove_language','English')])
    proposal=h.service.propose_edit(review.review_id,'42','No inglés')
    h.service.corrections.confirm([FactOperation('deny_claim','Team leader')],0)
    with pytest.raises(ValueError,match='facts changed'):
        h.service.confirm_edit(review.review_id,proposal.proposal_id,'42')
    assert h.reviews.load(review.review_id,'42').active_revision_id == first.revision_id
    proposal=h.service.propose_edit(review.review_id,'42','No inglés')
    notices=[]
    h.service.notify=notices.append
    def failed_hook(notify): raise RuntimeError('search plan unavailable')
    h.service.after_profile_change=failed_hook
    h.service.confirm_edit(review.review_id,proposal.proposal_id,'42')
    assert h.service.corrections.load().version == 2
    assert 'Corrección guardada' in notices[0]


def test_overlapping_occurrences_are_ambiguous(review_harness):
    h = review_harness
    review, first = editing(h, '# CV\n***\nPython', [('**', '*')])
    with pytest.raises(ValueError, match='fragmento único'):
        h.service.propose_edit(review.review_id, '42', 'corregir formato')
    assert h.reviews.load(review.review_id, '42').active_revision_id == first.revision_id


@pytest.mark.parametrize('fail_commit', [False, True])
def test_revoke_denial_and_restore_fragment_atomically(review_harness, monkeypatch, fail_commit):
    from job_agent.application.cv_review_models import CvEditProposal, TextReplacement
    h = review_harness
    review, first = editing(h, '# CV\nPython\nOtras habilidades', [('Otras habilidades', 'Team leader')])
    h.service.corrections.confirm([FactOperation('deny_claim', 'Team leader')], 0)
    correction_id = h.client.docs['profile_corrections/current']['active_ids'][0]
    h.service.interpreter = SimpleNamespace(propose=lambda *args: CvEditProposal(
        replacements=[TextReplacement(old_text='Otras habilidades', new_text='Team leader')],
        fact_operations=[FactOperation('revoke', correction_id)], explanation='Restaurar dato confirmado'))
    proposal = h.service.propose_edit(review.review_id, '42', 'Revoco esa negación; sí fui Team leader')
    assert h.service.corrections.load().version == 1  # Preview must never publish facts.
    assert h.service.corrections.load().operations == [FactOperation('deny_claim', 'Team leader')]
    if fail_commit:
        original = h.reviews.publish_revision_in_transaction
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('simulated revision failure after revocation')
        monkeypatch.setattr(h.reviews, 'publish_revision_in_transaction', fail)
        with pytest.raises(RuntimeError):
            h.service.confirm_edit(review.review_id, proposal.proposal_id, '42')
        assert h.service.corrections.load().version == 1
        assert h.service.corrections.load().operations == [FactOperation('deny_claim', 'Team leader')]
        assert h.reviews.load(review.review_id, '42').active_revision_id == first.revision_id
        assert h.reviews.load_proposal(review.review_id, proposal.proposal_id).status == 'pending'
    else:
        revision = h.service.confirm_edit(review.review_id, proposal.proposal_id, '42')
        assert h.use_case.artifacts.read_markdown(revision.markdown_uri) == '# CV\nPython\nTeam leader'
        assert h.service.corrections.load().version == 2
        assert h.service.corrections.load().operations == []


def test_retry_after_factual_refresh_reuses_saved_analysis_and_receipts(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    h.corrections = CorrectionSet(version=1, operations=[FactOperation('deny_claim', 'Team leader')])
    h.state.fail = 'send_pdf'
    with pytest.raises(RuntimeError): h.service.approve(review.review_id, revision.revision_id, '42')
    before = h.events.count('match')
    h.state.fail = None
    h.service.approve(review.review_id, revision.revision_id, '42')
    assert h.events.count('match') == before
    assert h.events.count('tailor') == h.events.count('render') == h.events.count('send_summary') == 1


def test_delivered_pdf_reopens_stored_markdown_and_preserves_original(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    result = h.service.approve(review.review_id, revision.revision_id, '42')
    ready = h.store.load_ready(result.key)
    reopened = h.service.open_delivered(ready, '42', NOW)
    edited = h.reviews.load_revision(reopened.review_id, reopened.active_revision_id, '42')
    assert h.use_case.artifacts.read_markdown(edited.markdown_uri) == ready.tailored.resume_markdown
    assert reopened.review_id != review.review_id
    assert h.store.load_ready(result.key) == ready
    assert h.events.count('tailor') == 1


def test_preview_receipts_map_only_the_owning_private_chat(review_harness):
    h = review_harness
    review = h.service.prepare('offer', '42', NOW)
    revision = h.service.generate_draft(review.review_id, '42')
    h.reviews.record_preview(review.review_id, revision.revision_id, '42', 100, 101)
    assert h.reviews.resolve_preview('42', 100) == (review.review_id, revision.revision_id)
    assert h.reviews.resolve_preview('99', 100) is None
    assert h.reviews.resolve_preview('42', 999) is None
    with pytest.raises(ValueError): h.reviews.record_preview(review.review_id, revision.revision_id, '99', 100, 101)


def test_failed_refreshed_analysis_is_immediately_retryable(review_harness):
    h=review_harness
    review=h.service.prepare('offer','42',NOW)
    revision=h.service.generate_draft(review.review_id,'42')
    h.corrections=CorrectionSet(version=1,operations=[FactOperation('deny_claim','Team leader')])
    h.state.fail='match'
    with pytest.raises(RuntimeError): h.service.approve(review.review_id,revision.revision_id,'42')
    h.state.fail=None
    assert h.service.approve(review.review_id,revision.revision_id,'42').delivery_status=='SENT'
