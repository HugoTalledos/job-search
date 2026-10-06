"""Persist drafts before explicit approval; render only the approved immutable text."""
from dataclasses import replace
from datetime import datetime, timezone
from secrets import token_urlsafe
from google.cloud import firestore
from ..domain.cv_corrections import CorrectionSet

from .cv_models import CvVersionKey, PreparedCvRequest
from .generate_tailored_cv import _fingerprint
from .ports import CvReviewStore, ProfileCorrections
from ..domain.cv_corrections import contradictions
from ..domain.models import Profile, JobPosting, JobMatch, TailoredResume


class CvReviewService:
    def __init__(self, *, generation, reviews: CvReviewStore, corrections: ProfileCorrections, interpreter=None, after_profile_change=None, notify=None):
        self.generation, self.reviews, self.corrections = generation, reviews, corrections
        self.interpreter = interpreter
        self.after_profile_change = after_profile_change
        self.notify = notify or (lambda message: None)

    def prepare(self, posting_id: str, chat_id: str, now: datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('CV request time requires an explicit timezone')
        g = self.generation
        resume, profile, posting = g.resume.read(), g.profile_reader.load(), g.posting_reader.load(posting_id)
        if not resume or not resume.strip():
            raise ValueError('Base resume is empty')
        if profile is None or posting is None:
            raise LookupError('CV inputs are missing')
        if not posting.description.strip():
            raise ValueError('Job posting description is empty')
        corrections = self.corrections.load()
        key = CvVersionKey(posting_id, _fingerprint(resume), _fingerprint(profile.model_dump(mode='json')),
                           _fingerprint(posting.model_dump(mode='json')), corrections.version)
        review = self.reviews.create_or_resume(posting_id, chat_id, key)
        if review.active_revision_id is None:
            self.reviews.save_context(review.review_id, {
                'resume_text': resume, 'profile': profile.model_dump(mode='json'),
                'posting': posting.model_dump(mode='json'), 'requested_at': now.isoformat(),
            })
        return review

    def generate_draft(self, review_id: str, chat_id: str):
        review = self.reviews.load(review_id, chat_id)
        if review.active_revision_id:
            return self.reviews.load_revision(review_id, review.active_revision_id, chat_id)
        token = self.reviews.claim_generation(review_id)
        try:
            context = self.reviews.load_context(review_id)
            posting, profile = JobPosting.model_validate(context['posting']), Profile.model_validate(context['profile'])
            corrections = self.corrections.load()
            g = self.generation
            kwargs = {'corrections': corrections} if corrections.version else {}
            match = g.matcher.score(posting, profile, context['resume_text'], **kwargs)
            tailored = g.tailor.tailor(posting, match, profile, context['resume_text'], **kwargs)
            self._validate(tailored.resume_markdown, corrections)
            uri = g.artifacts.save_markdown(review_id, token_urlsafe(12), tailored.resume_markdown)
            return self.reviews.publish_generated(review_id, token, uri, {
                **context, 'match': match.model_dump(mode='json'), 'tailored': tailored.model_dump(mode='json')})
        finally:
            self.reviews.release_generation(review_id, token)

    @staticmethod
    def _validate(markdown, corrections):
        if not markdown.strip():
            raise ValueError('Tailored resume is empty')
        if contradictions(markdown, corrections.operations):
            raise ValueError('CV contradicts confirmed candidate facts')

    def approve(self, review_id: str, revision_id: str, chat_id: str, *, reply_to_message_id: int = 0):
        review = self.reviews.load(review_id, chat_id)
        if review.active_revision_id != revision_id:
            raise ValueError('stale CV revision')
        revision = self.reviews.load_revision(review_id, revision_id, chat_id)
        markdown = self.generation.artifacts.read_markdown(revision.markdown_uri)
        corrections = self.corrections.load()
        self._validate(markdown, corrections)
        approval = self.reviews.approve(review_id, revision_id, chat_id, corrections_version=corrections.version)
        if approval.status not in {'approved', 'already_approved'}:
            raise ValueError(f'{approval.status} CV revision')
        context = self.reviews.load_context(review_id)
        profile = self.generation.profile_reader.load()
        posting = JobPosting.model_validate(context['posting'])
        key = approval.key
        claim = self.generation.tracking.claim(key, datetime.now(timezone.utc))
        prepared = PreparedCvRequest(key, claim.action, context['resume_text'], profile, posting,
                                     datetime.fromisoformat(context['requested_at']), claim.attempt_id)
        # Ready artifacts already contain the approved analysis. Delivery retries never score again.
        match, tailored = None, None
        if claim.action == 'generate':
            try:
                match = JobMatch.model_validate(context['match'])
                if corrections.version != review.key.corrections_version:
                    match = self.generation.matcher.score(posting, profile, context['resume_text'], corrections=corrections)
                tailored = TailoredResume.model_validate(context['tailored']).model_copy(update={
                    'resume_markdown': markdown,
                    'summary_for_candidate': 'CV revisado y aprobado.', 'changes': [],
                })
            except Exception:
                self.generation.tracking.mark_failed(key, attempt_id=claim.attempt_id)
                raise
        return self.generation.execute(prepared, chat_id, reply_to_message_id, approved_match=match,
                                       approved_tailored=tailored, resend=False)

    @staticmethod
    def _replace(markdown, replacements):
        if not replacements:
            raise ValueError('Precisa el fragmento que deseas corregir')
        spans = []
        for item in replacements:
            start = markdown.find(item.old_text)
            if item.old_text == markdown or start < 0 or markdown.find(item.old_text, start + 1) >= 0:
                raise ValueError('Precisa un fragmento único; no se reemplaza todo el CV')
            spans.append((start, start + len(item.old_text), item.new_text))
        spans.sort()
        if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
            raise ValueError('Los fragmentos se solapan; precisa el cambio')
        if sum(end-start for start,end,_ in spans) == len(markdown):
            raise ValueError('No se reemplaza todo el CV')
        for start, end, text in reversed(spans):
            markdown = markdown[:start] + text + markdown[end:]
        return markdown

    def propose_edit(self, review_id, chat_id, instruction):
        review = self.reviews.load(review_id, chat_id)
        if review.status != 'DRAFT' or not review.active_revision_id:
            raise ValueError('No active draft')
        revision = self.reviews.load_revision(review_id, review.active_revision_id, chat_id)
        markdown = self.generation.artifacts.read_markdown(revision.markdown_uri)
        corrections = self.corrections.load()
        proposal = self.interpreter.propose(markdown, instruction, self.generation.profile_reader.load(), corrections)
        updated = self._replace(markdown, proposal.replacements)
        projected = self.corrections.preview(proposal.fact_operations, corrections.version) if proposal.fact_operations else corrections
        self._validate(updated, projected)
        proposal_id = self.reviews.save_proposal(review_id, revision.revision_id, corrections.version, proposal)
        return proposal.model_copy(update={'proposal_id': proposal_id})

    def confirm_edit(self, review_id, proposal_id, chat_id):
        self.reviews.load(review_id, chat_id)
        resolution = self.reviews.load_proposal(review_id, proposal_id)
        proposal = resolution.proposal
        revision = self.reviews.load_revision(review_id, resolution.expected_revision_id, chat_id)
        markdown = self.generation.artifacts.read_markdown(revision.markdown_uri)
        updated = self._replace(markdown, proposal.replacements)
        # Immutable upload is safe to orphan if the metadata transaction conflicts.
        uri = self.generation.artifacts.save_markdown(review_id, token_urlsafe(12), updated)
        @firestore.transactional
        def confirm(transaction):
            pending = self.reviews.prepare_proposal_in_transaction(transaction, review_id, proposal_id)
            staged_revision = self.reviews.prepare_revision_in_transaction(transaction, review_id,
                resolution.expected_revision_id, uri, proposal_id)
            staged_facts = self.corrections.prepare_in_transaction(transaction)
            current = CorrectionSet(version=staged_facts[0].get('version', 0), operations=staged_facts[2])
            if current.version != resolution.expected_corrections_version:
                raise ValueError('Confirmed facts changed; renew proposal')
            # ALL adapter reads above precede ANY write below.
            if proposal.fact_operations:
                current = self.corrections.apply_in_transaction(transaction, proposal.fact_operations,
                    resolution.expected_corrections_version, prepared=staged_facts)
            self._validate(updated, current)
            result = self.reviews.publish_revision_in_transaction(transaction, review_id,
                resolution.expected_revision_id, uri, proposal_id, prepared=staged_revision)
            self.reviews.resolve_proposal_in_transaction(transaction, review_id, proposal_id, 'confirm', prepared=pending)
            return result
        result = confirm(self.reviews.client.transaction())
        if proposal.fact_operations and contradictions(self.generation.resume.read(), self.corrections.load().operations):
            self.notify('Corrección guardada. resume/base.md contiene datos que contradicen hechos confirmados; revisa el archivo base. La corrección sigue vigente.')
        if proposal.fact_operations and self.after_profile_change:
            try:
                self.after_profile_change(self.notify)
            except Exception:
                self.notify('Corrección guardada; no se pudo reconstruir el plan de búsqueda. Reintenta después.')
        return result

    def reject_edit(self, review_id, proposal_id, chat_id):
        self.reviews.load(review_id, chat_id)
        @firestore.transactional
        def reject(transaction):
            return self.reviews.resolve_proposal_in_transaction(transaction, review_id, proposal_id, 'reject')
        return reject(self.reviews.client.transaction())


    def open_delivered(self, ready, chat_id, now):
        """Fork the saved delivered Markdown, never rerun tailoring or overwrite its version."""
        g = self.generation
        posting = g.posting_reader.load(ready.key.posting_id)
        profile = g.profile_reader.load()
        # Source identity makes separate delivered versions separate editable drafts.
        key = replace(ready.key, review_id=None, revision_id=ready.key.version_id)
        review = self.reviews.create_or_resume(ready.key.posting_id, chat_id, key)
        if review.active_revision_id:
            return review
        token = self.reviews.claim_generation(review.review_id)
        try:
            markdown = g.artifacts.read_markdown(ready.artifacts.markdown_uri)
            context = {
                'resume_text': g.resume.read(), 'profile': profile.model_dump(mode='json'),
                'posting': posting.model_dump(mode='json'), 'requested_at': now.isoformat(),
                'match': ready.match.model_dump(mode='json'), 'tailored': ready.tailored.model_dump(mode='json'),
            }
            uri = g.artifacts.save_markdown(review.review_id, token_urlsafe(12), markdown)
            self.reviews.publish_generated(review.review_id, token, uri, context)
        finally:
            self.reviews.release_generation(review.review_id, token)
        return self.reviews.load(review.review_id, chat_id)

    def show_preview(self, review_id, chat_id, reply_to_message_id):
        review = self.reviews.load(review_id, chat_id)
        revision = self.reviews.load_revision(review_id, review.active_revision_id, chat_id)
        markdown = self.generation.artifacts.read_markdown(revision.markdown_uri)
        # Derived from this revision, never from stale tailoring claims.
        summary = '\n'.join(line for line in markdown.splitlines() if line.strip())[:2500]
        receipt = self.generation.delivery.send_preview(chat_id, reply_to_message_id,
            review_id, revision.revision_id, summary, markdown)
        self.reviews.record_preview(review_id, revision.revision_id, chat_id,
            receipt.preview_message_id, receipt.markdown_message_id)
        return receipt
