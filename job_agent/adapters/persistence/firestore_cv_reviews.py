"""Transactional review pointers referencing previously uploaded private Markdown."""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from secrets import token_urlsafe
from google.cloud import firestore
from ...application.cv_models import CvVersionKey
from ...application.cv_review_models import ApprovalResult, CvReview, CvRevision


class StaleCvRevision(ValueError):
    """The draft changed or has already been approved."""


class FirestoreCvReviewStore:
    def __init__(self, client):
        self.client = client
        self.reviews = client.collection('cv_reviews')
        self.index = client.collection('cv_review_active')

    @staticmethod
    def _review(data):
        return CvReview(**{**data, 'key': CvVersionKey(**data['key'])})

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

    def publish_revision_in_transaction(self, transaction, review_id, expected_revision_id, markdown_uri, proposal_id):
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
        transaction.set(revision_ref, asdict(revision))
        transaction.set(ref, {**data, 'active_revision_id': revision_id})
        return revision

    def approve(self, review_id, revision_id, chat_id):
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
                return ApprovalResult('already_approved', revision)
            if data['status'] != 'DRAFT':
                return ApprovalResult('stale')
            transaction.set(ref, {**data, 'approved_revision_id': revision_id, 'status': 'APPROVED'})
            return ApprovalResult('approved', revision)
        return approve(self.client.transaction())
