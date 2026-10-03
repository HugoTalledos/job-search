"""In-memory implementations of the application ports, for use-case tests."""

from __future__ import annotations

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
