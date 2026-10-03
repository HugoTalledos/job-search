"""SearchSettingsStore port on Firestore.

- ``settings/search_preferences``: the candidate's preferences, the source of truth.
- ``settings/search_plan``: the plan compiled from them; the local collector reads it with
  ``CollectorPlan.model_validate``, so it is written as ``model_dump(mode="json")``.
- ``search_preference_drafts/{draft_id}``: proposed changes awaiting confirmation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from google.cloud import firestore
from pydantic import ValidationError

from job_contracts import CollectorPlan, SearchPreferences

from ...application.preference_models import DraftResolution, PreferenceDraft

log = logging.getLogger(__name__)

Compile = Callable[[SearchPreferences], CollectorPlan | None]


class FirestoreSearchSettingsStore:
    def __init__(self, client: firestore.Client) -> None:
        self.client = client
        settings = client.collection("settings")
        self.preferences = settings.document("search_preferences")
        self.plan = settings.document("search_plan")

    def _draft(self, draft_id: str):
        return self.client.collection("search_preference_drafts").document(draft_id)

    def load_preferences(self) -> SearchPreferences | None:
        data = self.preferences.get().to_dict()
        if data is None:
            return None
        try:
            return SearchPreferences.model_validate(data)
        except ValidationError:
            log.warning("Invalid document at settings/search_preferences; treating it as missing")
            return None

    def load_plan(self) -> CollectorPlan | None:
        data = self.plan.get().to_dict()
        if data is None:
            return None
        try:
            return CollectorPlan.model_validate(data)
        except ValidationError:
            log.warning("Invalid document at settings/search_plan; treating it as missing")
            return None

    def seed(self, preferences: SearchPreferences, compile: Compile, *, force: bool, now: datetime) -> bool:
        @firestore.transactional
        def seed_preferences(transaction) -> bool:
            current = self.preferences.get(transaction=transaction).to_dict()
            if current is not None and not force:
                return False
            new = preferences.model_copy(update={"version": _version(current) + 1, "updated_at": now})
            self._write(transaction, new, compile(new))
            return True

        return seed_preferences(self.client.transaction())

    def save_plan_if_version(self, plan: CollectorPlan, expected_version: int) -> bool:
        @firestore.transactional
        def save(transaction) -> bool:
            current = self.preferences.get(transaction=transaction).to_dict()
            if current is None or _version(current) != expected_version:
                return False
            transaction.set(self.plan, plan.model_dump(mode="json"))
            return True

        return save(self.client.transaction())

    def create_draft(self, draft: PreferenceDraft) -> None:
        self._draft(draft.draft_id).set(draft.model_dump(mode="json"))

    def resolve_draft(
        self, draft_id: str, chat_id: str, action: Literal["apply", "cancel"], now: datetime, compile: Compile,
    ) -> DraftResolution:
        draft_ref = self._draft(draft_id)

        @firestore.transactional
        def resolve(transaction) -> DraftResolution:
            data = draft_ref.get(transaction=transaction).to_dict()
            current_version = _version(self.preferences.get(transaction=transaction).to_dict())
            if data is None or data.get("chat_id") != chat_id:
                return DraftResolution(status="not_found")
            draft = PreferenceDraft.model_validate(data)
            if draft.status != "PENDING":
                return DraftResolution(status="already_resolved", previous_status=draft.status)

            def close(status: str) -> None:
                transaction.set(draft_ref, {"status": status, "resolved_at": now}, merge=True)

            if action == "cancel":
                close("CANCELLED")
                return DraftResolution(status="cancelled")
            if now >= draft.expires_at:
                close("EXPIRED")
                return DraftResolution(status="expired")
            if current_version != draft.base_version:
                close("EXPIRED")
                return DraftResolution(status="stale", preferences_version=current_version)
            new = draft.preferences.model_copy(update={"version": draft.base_version + 1, "updated_at": now})
            plan = compile(new)
            self._write(transaction, new, plan)
            close("APPLIED")
            return DraftResolution(
                status="applied", preferences_version=new.version, plan=plan, plan_kept=plan is None,
            )

        return resolve(self.client.transaction())

    def _write(self, transaction, preferences: SearchPreferences, plan: CollectorPlan | None) -> None:
        transaction.set(self.preferences, preferences.model_dump(mode="json"))
        if plan is not None:
            transaction.set(self.plan, plan.model_dump(mode="json"))


def _version(data: dict | None) -> int:
    return int((data or {}).get("version", 0))
