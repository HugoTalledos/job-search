"""In-memory implementations of the application ports, for use-case tests."""

from __future__ import annotations

from job_agent.application.preference_models import DraftResolution
from job_agent.domain.models import Profile, RepoEvidence, RepoRef, StoredProfile


class FakeResume:
    def __init__(self, text="# Resume"):
        self.text = text

    def read(self):
        return self.text


class FakeRepositories:
    def __init__(self, heads: dict[str, str]):
        self.heads = heads
        self.collected = 0
        self.listed = 0  # network calls in the real adapter (GitHub API + git ls-remote)

    def list_repositories(self):
        self.listed += 1
        return [RepoRef(url=u) for u in self.heads]

    def head(self, repo):
        return self.heads[repo.url]

    def collect_evidence(self, repo):
        self.collected += 1
        return RepoEvidence(url=repo.url, head=self.heads[repo.url], summary=f"repo {repo.url}")


class FakeInferer:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.calls = 0

    def infer(self, resume_text, evidence, preferred_locations):
        self.calls += 1
        return self.profile


class MemoryProfileStore:
    def __init__(self):
        self.stored: StoredProfile | None = None

    def load(self):
        return self.stored

    def save(self, stored):
        self.stored = stored


class MemorySearchSettings:
    """In-memory SearchSettingsStore mirroring the Firestore adapter's draft semantics."""

    def __init__(self):
        self.prefs = None
        self.plan = None
        self.drafts: dict = {}

    def load_preferences(self):
        return self.prefs

    def load_plan(self):
        return self.plan

    def seed(self, preferences, compile, *, force, now):
        if self.prefs is not None and not force:
            return False
        self.prefs = preferences.model_copy(update={"version": 1, "updated_at": now})
        self.plan = compile(self.prefs) or self.plan
        return True

    def save_plan(self, plan):
        self.plan = plan

    def create_draft(self, draft):
        self.drafts[draft.draft_id] = draft

    def resolve_draft(self, draft_id, chat_id, action, now, compile):
        draft = self.drafts.get(draft_id)
        if draft is None or draft.chat_id != chat_id:
            return DraftResolution(status="not_found")
        if draft.status != "PENDING":
            return DraftResolution(status="already_resolved", previous_status=draft.status)
        if action == "cancel":
            draft.status = "CANCELLED"
            return DraftResolution(status="cancelled")
        if now >= draft.expires_at:
            draft.status = "EXPIRED"
            return DraftResolution(status="expired")
        if self.prefs.version != draft.base_version:
            draft.status = "EXPIRED"
            return DraftResolution(status="stale", preferences_version=self.prefs.version)
        self.prefs = draft.preferences.model_copy(update={"version": draft.base_version + 1, "updated_at": now})
        plan = compile(self.prefs)
        if plan is not None:
            self.plan = plan
        draft.status = "APPLIED"
        return DraftResolution(
            status="applied", preferences_version=self.prefs.version, plan=plan, plan_kept=plan is None
        )
