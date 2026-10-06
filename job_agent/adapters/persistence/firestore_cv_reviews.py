"""Transactional review pointers referencing previously uploaded private Markdown."""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from secrets import token_urlsafe
from google.cloud import firestore
from ...application.cv_models import CvVersionKey
from ...application.cv_review_models import ApprovalResult, CvReview, CvRevision, CvEditProposal, ProposalResolution


class StaleCvRevision(ValueError):
    """The draft changed or has already been approved."""


class FirestoreCvReviewStore:
    def __init__(self, client):
        self.client = client
        self.reviews = client.collection('cv_reviews')
        self.index = client.collection('cv_review_active')

    @staticmethod
    def _review(data):
        return CvReview(**{**data, 'key': CvVersionKey(**data['key']),
            'approved_key': CvVersionKey(**data['approved_key']) if data.get('approved_key') else None})

    def create_or_resume(self, posting_id, chat_id, key):
        if posting_id != key.posting_id or not str(chat_id).isascii() or not str(chat_id).isdigit() or int(chat_id) <= 0:
            raise ValueError('A matching posting and private chat owner are required')
        chat_id = str(chat_id)
        index_id = sha256(json.dumps([posting_id, chat_id, key.version_id]).encode()).hexdigest()
        ref = self.index.document(index_id)
        @firestore.transactional
        def create(transaction):
            index = ref.get(transaction=transaction).to_dict() or {}
            data = self.reviews.document(index['review_id']).get(transaction=transaction).to_dict() if index else None
            if data and data['status'] == 'DRAFT':
                return self._review(data)
            review = CvReview(token_urlsafe(12), posting_id, chat_id, key)
            transaction.set(self.reviews.document(review.review_id), asdict(review))
            transaction.set(ref, {'review_id': review.review_id})
            return review
        return create(self.client.transaction())

    def load(self, review_id, chat_id):
        data = self.reviews.document(review_id).get().to_dict()
        if not data or data['chat_id'] != str(chat_id):
            raise ValueError('Unknown review')
        return self._review(data)

    def publish_revision(self, review_id, expected_revision_id, markdown_uri, proposal_id):
        @firestore.transactional
        def publish(transaction):
            return self.publish_revision_in_transaction(transaction, review_id, expected_revision_id, markdown_uri, proposal_id)
        return publish(self.client.transaction())

    def prepare_revision_in_transaction(self, transaction, review_id, expected_revision_id, markdown_uri, proposal_id):
        parts = markdown_uri.split('/', 3)
        if len(parts) != 4 or parts[0] != 'gs:' or not parts[2] or not parts[3]:
            raise ValueError('Private uploaded Markdown URI required')
        ref = self.reviews.document(review_id)
        data = ref.get(transaction=transaction).to_dict()
        if not data or data['status'] != 'DRAFT' or data['active_revision_id'] != expected_revision_id:
            raise StaleCvRevision('Draft changed; reload its active revision')
        # The immutable object was uploaded before this transaction; callbacks use a compact random ID.
        revision_id = token_urlsafe(12)
        revision_ref = ref.collection('revisions').document(revision_id)
        if revision_ref.get(transaction=transaction).exists:
            raise StaleCvRevision('An immutable revision cannot be republished')
        revision = CvRevision(revision_id, review_id, markdown_uri, proposal_id, datetime.now(timezone.utc))
        return ref, data, revision_ref, revision

    def publish_revision_in_transaction(self, transaction, review_id, expected_revision_id, markdown_uri, proposal_id, *, prepared=None):
        ref, data, revision_ref, revision = prepared or self.prepare_revision_in_transaction(
            transaction, review_id, expected_revision_id, markdown_uri, proposal_id)
        transaction.set(revision_ref, asdict(revision))
        transaction.set(ref, {**data, 'active_revision_id': revision.revision_id})
        return revision

    def approve(self, review_id, revision_id, chat_id, *, corrections_version=None):
        @firestore.transactional
        def approve(transaction):
            ref = self.reviews.document(review_id)
            data = ref.get(transaction=transaction).to_dict()
            if not data or data['chat_id'] != str(chat_id):
                return ApprovalResult('unknown')
            if data['active_revision_id'] != revision_id or revision_id is None:
                return ApprovalResult('stale')
            revision_data = ref.collection('revisions').document(revision_id).get(transaction=transaction).to_dict()
            if not revision_data:
                return ApprovalResult('unknown')
            revision = CvRevision(**revision_data)
            if data['approved_revision_id'] == revision_id:
                return ApprovalResult('already_approved', revision,
                    CvVersionKey(**data['approved_key']) if data.get('approved_key') else
                    replace(CvVersionKey(**data['key']), review_id=review_id, revision_id=revision_id))
            if data['status'] != 'DRAFT':
                return ApprovalResult('stale')
            key = replace(CvVersionKey(**data['key']), review_id=review_id, revision_id=revision_id,
                corrections_version=corrections_version if corrections_version is not None else data['key'].get('corrections_version', 0))
            transaction.set(ref, {**data, 'approved_revision_id': revision_id, 'status': 'APPROVED', 'approved_key': asdict(key)})
            return ApprovalResult('approved', revision, key)
        return approve(self.client.transaction())

    def save_context(self, review_id, context):
        @firestore.transactional
        def save(transaction):
            ref = self.reviews.document(review_id)
            data = ref.get(transaction=transaction).to_dict()
            context_ref = ref.collection('context').document('inputs')
            existing = context_ref.get(transaction=transaction).to_dict()
            if data and data['status'] == 'DRAFT' and data['active_revision_id'] is None and not existing:
                transaction.set(context_ref, context)
        save(self.client.transaction())

    def claim_generation(self, review_id):
        """Lease and fence the initial generation; expired workers cannot publish."""
        @firestore.transactional
        def claim(transaction):
            ref = self.reviews.document(review_id)
            review = ref.get(transaction=transaction).to_dict()
            lease_ref = ref.collection('context').document('generation')
            lease = lease_ref.get(transaction=transaction).to_dict() or {}
            now = datetime.now(timezone.utc)
            if not review or review['status'] != 'DRAFT' or review['active_revision_id'] is not None:
                raise StaleCvRevision('Draft changed; reload its active revision')
            if lease.get('expires_at') and lease['expires_at'] > now:
                raise ValueError('Draft generation in progress')
            token = token_urlsafe(12)
            transaction.set(lease_ref, {'token': token, 'expires_at': now + timedelta(minutes=10)})
            return token
        return claim(self.client.transaction())

    def release_generation(self, review_id, token):
        @firestore.transactional
        def release(transaction):
            ref = self.reviews.document(review_id).collection('context').document('generation')
            lease = ref.get(transaction=transaction).to_dict() or {}
            if lease.get('token') == token:
                transaction.set(ref, {'token': None})
        release(self.client.transaction())

    def publish_generated(self, review_id, token, markdown_uri, context):
        @firestore.transactional
        def publish(transaction):
            ref = self.reviews.document(review_id)
            lease_ref = ref.collection('context').document('generation')
            lease = lease_ref.get(transaction=transaction).to_dict() or {}
            if lease.get('token') != token:
                raise StaleCvRevision('Draft generation was superseded')
            prepared = self.prepare_revision_in_transaction(transaction, review_id, None, markdown_uri, None)
            revision = self.publish_revision_in_transaction(transaction, review_id, None, markdown_uri, None, prepared=prepared)
            transaction.set(ref.collection('context').document('inputs'), {**context, 'revision_id': revision.revision_id})
            transaction.set(lease_ref, {'token': None})
            return revision
        return publish(self.client.transaction())

    def load_context(self, review_id):
        data = self.reviews.document(review_id).collection('context').document('inputs').get().to_dict()
        if not data:
            raise LookupError('Review context is missing')
        return data

    def load_revision(self, review_id, revision_id, chat_id):
        self.load(review_id, chat_id)
        data = self.reviews.document(review_id).collection('revisions').document(revision_id).get().to_dict()
        if not data:
            raise LookupError('Review revision is missing')
        return CvRevision(**data)

    def save_proposal(self, review_id, expected_revision_id, expected_corrections_version, proposal):
        proposal_id = token_urlsafe(12)
        @firestore.transactional
        def save(transaction):
            ref = self.reviews.document(review_id)
            data = ref.get(transaction=transaction).to_dict()
            if not data or data['status'] != 'DRAFT' or data['active_revision_id'] != expected_revision_id:
                raise StaleCvRevision('Draft changed; renew proposal')
            transaction.set(ref.collection('proposals').document(proposal_id), {
                'proposal': proposal.model_copy(update={'proposal_id': proposal_id}).model_dump(mode='json'),
                'expected_revision_id': expected_revision_id,
                'expected_corrections_version': expected_corrections_version, 'status': 'pending'})
            return proposal_id
        return save(self.client.transaction())

    def load_proposal(self, review_id, proposal_id):
        data = self.reviews.document(review_id).collection('proposals').document(proposal_id).get().to_dict()
        if not data:
            raise ValueError('Unknown proposal')
        return ProposalResolution(data['status'], CvEditProposal.model_validate(data['proposal']),
            data['expected_revision_id'], data['expected_corrections_version'])

    def prepare_proposal_in_transaction(self, transaction, review_id, proposal_id):
        ref = self.reviews.document(review_id)
        review = ref.get(transaction=transaction).to_dict()
        proposal_ref = ref.collection('proposals').document(proposal_id)
        data = proposal_ref.get(transaction=transaction).to_dict()
        if not review or not data or review['status'] != 'DRAFT' or data['status'] != 'pending' or review['active_revision_id'] != data['expected_revision_id']:
            raise StaleCvRevision('Stale or resolved proposal; renew proposal')
        return proposal_ref, data

    def resolve_proposal_in_transaction(self, transaction, review_id, proposal_id, action, *, prepared=None):
        if action not in {'confirm', 'reject'}:
            raise ValueError('Unknown proposal action')
        ref, data = prepared or self.prepare_proposal_in_transaction(transaction, review_id, proposal_id)
        transaction.set(ref, {**data, 'status': action})
        return ProposalResolution(action, CvEditProposal.model_validate(data['proposal']),
            data['expected_revision_id'], data['expected_corrections_version'])

    def cancel(self, review_id, chat_id):
        @firestore.transactional
        def cancel(transaction):
            ref = self.reviews.document(review_id)
            data = ref.get(transaction=transaction).to_dict()
            if not data or data['chat_id'] != str(chat_id) or data['status'] != 'DRAFT':
                raise ValueError('Unknown or inactive review')
            transaction.set(ref, {**data, 'status': 'CANCELLED'})
        cancel(self.client.transaction())


    def record_preview(self, review_id, revision_id, chat_id, preview_message_id, markdown_message_id):
        self.load_revision(review_id, revision_id, chat_id)
        for message_id in (preview_message_id, markdown_message_id):
            self.client.collection('cv_preview_messages').document(f'{chat_id}_{message_id}').set({
                'review_id': review_id, 'revision_id': revision_id, 'chat_id': str(chat_id),
                'preview_message_id': preview_message_id, 'markdown_message_id': markdown_message_id,
            })

    def resolve_preview(self, chat_id, message_id):
        data = self.client.collection('cv_preview_messages').document(f'{chat_id}_{message_id}').get().to_dict()
        if not data or data['chat_id'] != str(chat_id):
            return None
        self.load(data['review_id'], chat_id)
        return data['review_id'], data['revision_id']
