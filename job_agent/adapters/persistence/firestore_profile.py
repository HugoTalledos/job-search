"""ProfileStore port on Firestore: ``profiles/current``.

The profile fields live at the top level of the document, which is what the scoring workflow reads;
the build metadata (fingerprints, dates and repository digests) sits beside them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from google.cloud import firestore
from pydantic import ValidationError

from ...domain.models import Profile, StoredProfile

log = logging.getLogger(__name__)

_METADATA = ("resume_fingerprint", "repos_fingerprint", "built_at", "repos_checked_at", "repositories")


class FirestoreProfileStore:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client
        self.document = client.collection("profiles").document("current")

    def load(self) -> StoredProfile | None:
        snapshot = self.document.get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        # Documents written by hand (before this store existed) carry only the profile fields.
        metadata = {"built_at": datetime.fromtimestamp(0, timezone.utc)}
        metadata.update({key: data[key] for key in _METADATA if data.get(key) is not None})
        try:
            return StoredProfile(profile=Profile.model_validate(data), **metadata)
        except ValidationError:
            log.warning("Invalid profile document at profiles/current; treating it as missing")
            return None

    def save(self, stored: StoredProfile) -> None:
        """Refresh metadata without replacing a concurrently corrected projection."""
        if not hasattr(self.client, "transaction"):
            self.document.set({**stored.profile.model_dump(), **stored.model_dump(include=set(_METADATA))})
            return

        @firestore.transactional
        def save(transaction):
            current = self.document.get(transaction=transaction).to_dict()
            if current is None:
                current = {**stored.profile.model_dump(), "inferred_profile": stored.profile.model_dump(),
                           "corrections_version": 0}
            transaction.set(self.document, {**current, **stored.model_dump(include=set(_METADATA))})
        save(self.client.transaction())

    def save_inferred(self, stored: StoredProfile) -> StoredProfile:
        from .firestore_profile_corrections import FirestoreProfileCorrections
        from ...domain.cv_corrections import apply_fact_operations

        @firestore.transactional
        def save(transaction):
            corrections = FirestoreProfileCorrections(self.client)
            data, _, operations = corrections._read(transaction)
            current = self.document.get(transaction=transaction).to_dict() or {}
            effective = stored.model_copy(update={"profile": apply_fact_operations(stored.profile, operations)})
            transaction.set(self.document, {
                **current, **effective.profile.model_dump(), **stored.model_dump(include=set(_METADATA)),
                "inferred_profile": stored.profile.model_dump(), "corrections_version": data.get("version", 0),
            })
            return effective
        return save(self.client.transaction())
