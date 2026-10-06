"""Persist drafts before explicit approval; render only the approved immutable text."""
from dataclasses import replace
from datetime import datetime, timezone
from secrets import token_urlsafe

from .cv_models import CvVersionKey, PreparedCvRequest
from .generate_tailored_cv import _fingerprint
from .ports import CvReviewStore, ProfileCorrections
from ..domain.cv_corrections import contradictions
from ..domain.models import Profile, JobPosting, JobMatch, TailoredResume


class CvReviewService:
    def __init__(self, *, generation, reviews: CvReviewStore, corrections: ProfileCorrections):
        self.generation, self.reviews, self.corrections = generation, reviews, corrections

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
        context = self.reviews.load_context(review_id)
        posting, profile = JobPosting.model_validate(context['posting']), Profile.model_validate(context['profile'])
        corrections = self.corrections.load()
        g = self.generation
        kwargs = {'corrections': corrections} if corrections.version else {}
        match = g.matcher.score(posting, profile, context['resume_text'], **kwargs)
        tailored = g.tailor.tailor(posting, match, profile, context['resume_text'], **kwargs)
        self._validate(tailored.resume_markdown, corrections)
        # Persist analysis first; failure never creates an active incomplete draft.
        self.reviews.save_context(review_id, {**context, 'match': match.model_dump(mode='json'),
                                              'tailored': tailored.model_dump(mode='json')})
        uri = g.artifacts.save_markdown(review_id, token_urlsafe(12), tailored.resume_markdown)
        return self.reviews.publish_revision(review_id, None, uri, None)

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
        approval = self.reviews.approve(review_id, revision_id, chat_id)
        if approval.status not in {'approved', 'already_approved'}:
            raise ValueError(f'{approval.status} CV revision')
        context = self.reviews.load_context(review_id)
        profile = self.generation.profile_reader.load()
        posting = JobPosting.model_validate(context['posting'])
        # Refresh evidence for factual changes without rewriting the approved CV.
        match = JobMatch.model_validate(context['match'])
        if corrections.version != review.key.corrections_version:
            match = self.generation.matcher.score(posting, profile, context['resume_text'], corrections=corrections)
        tailored = TailoredResume.model_validate(context['tailored']).model_copy(update={
            'resume_markdown': markdown,
            'summary_for_candidate': 'CV revisado y aprobado.', 'changes': [],
        })
        key = replace(review.key, review_id=review_id, revision_id=revision_id)
        claim = self.generation.tracking.claim(key, datetime.now(timezone.utc))
        prepared = PreparedCvRequest(key, claim.action, context['resume_text'], profile, posting,
                                     datetime.fromisoformat(context['requested_at']), claim.attempt_id)
        return self.generation.execute(prepared, chat_id, reply_to_message_id, approved_match=match,
                                       approved_tailored=tailored, resend=False)
