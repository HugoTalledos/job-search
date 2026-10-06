"""Immutable confirmed facts and their transactional effective-profile projection."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json

from google.cloud import firestore

from ...domain.cv_corrections import CorrectionSet, FactOperation, apply_fact_operations
from ...domain.models import Profile


class CorrectionVersionConflict(ValueError):
    """The caller must reload the corrections and renew its proposal."""


class FirestoreProfileCorrections:
    def __init__(self, client):
        self.client = client
        self.collection = client.collection('profile_corrections')
        self.current = self.collection.document('current')
        self.profile = client.collection('profiles').document('current')

    def _read(self, transaction=None):
        kwargs = {'transaction': transaction} if transaction is not None else {}
        data = self.current.get(**kwargs).to_dict() or {}
        ids = data.get('active_ids', [])
        records = [self.collection.document(i).get(**kwargs).to_dict() for i in ids]
        return data, ids, [FactOperation.model_validate(record['operation']) for record in records]

    def load(self) -> CorrectionSet:
        # Read the index and its immutable records from one consistent snapshot.
        @firestore.transactional
        def read(transaction):
            data, ids, operations = self._read(transaction)
            return CorrectionSet(version=data.get('version', 0), operations=operations, active_ids=ids)
        return read(self.client.transaction())

    def confirm(self, operations: list[FactOperation], expected_version: int) -> CorrectionSet:
        @firestore.transactional
        def confirm(transaction):
            return self.apply_in_transaction(transaction, operations, expected_version)
        return confirm(self.client.transaction())

    def preview(self, operations: list[FactOperation], expected_version: int) -> CorrectionSet:
        """Validate the same ordered projection as confirmation without any writes."""
        @firestore.transactional
        def preview(transaction):
            return self.apply_in_transaction(transaction, operations, expected_version, persist=False)
        return preview(self.client.transaction())

    def prepare_in_transaction(self, transaction):
        return (*self._read(transaction), self.profile.get(transaction=transaction).to_dict())

    def apply_in_transaction(self, transaction, operations: list[FactOperation], expected_version: int, *, prepared=None, persist=True) -> CorrectionSet:
        operations = [FactOperation.model_validate(op) for op in operations]
        payload = json.dumps([op.model_dump() for op in operations], sort_keys=True)
        request_id = sha256(f'{expected_version}:{payload}'.encode()).hexdigest()
        data, ids, active, profile_data = prepared if prepared is not None else self.prepare_in_transaction(transaction)
        version = data.get('version', 0)
        if version != expected_version:
            if data.get('last_request_id') == request_id and version == expected_version + 1:
                return CorrectionSet(version=version, operations=active, active_ids=ids)
            raise CorrectionVersionConflict('Confirmed facts changed; reload the proposal')
        if profile_data is None:
            raise ValueError('A profile is required before confirming facts')
        base = Profile.model_validate(profile_data.get('inferred_profile', profile_data))
        new_ids, new_active = list(ids), list(active)
        records = []
        now = datetime.now(timezone.utc)
        for index, op in enumerate(operations):
            correction_id = sha256(f'{request_id}:{index}'.encode()).hexdigest()
            if op.kind == 'revoke':
                if op.subject not in new_ids:
                    raise ValueError('Revocation requires an active correction ID')
                position = new_ids.index(op.subject)
                new_ids.pop(position)
                new_active.pop(position)
            else:
                new_ids.append(correction_id)
                new_active.append(op)
            records.append((correction_id, {'operation': op.model_dump(), 'confirmed_at': now,
                                            'scope': 'global', 'version': version + 1}))
        effective = apply_fact_operations(base, new_active)
        if not persist:
            return CorrectionSet(version=version + 1, operations=new_active, active_ids=new_ids)
        for correction_id, record in records:
            transaction.set(self.collection.document(correction_id), record)
        transaction.set(self.current, {'version': version + 1, 'active_ids': new_ids, 'last_request_id': request_id})
        transaction.set(self.profile, {**profile_data, **effective.model_dump(),
                                      'inferred_profile': base.model_dump(), 'corrections_version': version + 1})
        return CorrectionSet(version=version + 1, operations=new_active, active_ids=new_ids)
