"""In-memory implementations of the application ports, for use-case tests."""

from __future__ import annotations

from job_agent.domain.models import (
    JobAlert,
    JobMatch,
    JobPosting,
    MatchRecord,
    Profile,
    RepoEvidence,
    RepoRef,
    SavedApplication,
    SearchCriteria,
    StoredProfile,
    TailoredResume,
)
from job_agent.domain.policies import job_key


class FakeResume:
    def __init__(self, text="# Resume"):
        self.text = text

    def read(self):
        return self.text


class FakeRepositories:
    def __init__(self, heads: dict[str, str]):
        self.heads = heads
        self.collected = 0

    def list_repositories(self):
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


class StaticProfile:
    """Stands in for EnsureProfile."""

    def __init__(self, profile):
        self.profile = profile

    def execute(self, force=False):
        return self.profile


class FakeSource:
    def __init__(self, name, jobs=None, error=None):
        self.name = name
        self.jobs = jobs or []
        self.error = error
        self.criteria: SearchCriteria | None = None

    def search(self, criteria):
        self.criteria = criteria
        if self.error:
            raise self.error
        return list(self.jobs)


class TableMatcher:
    def __init__(self, scores: dict[str, JobMatch]):
        self.scores = scores

    def score(self, job, profile, resume_text):
        return self.scores[job.title]


class FakeTailor:
    def __init__(self, tailored: TailoredResume):
        self.tailored = tailored
        self.calls: list[JobPosting] = []

    def tailor(self, job, match, profile, resume_text):
        self.calls.append(job)
        return self.tailored


class MemoryApplications:
    def __init__(self):
        self.saved = []

    def save(self, job, match, tailored):
        self.saved.append(job)
        return SavedApplication(folder=f"output/{job_key(job)}", link=f"https://repo/{job_key(job)}")


class RecordingNotifier:
    def __init__(self):
        self.alerts: list[JobAlert] = []

    def notify(self, alert):
        self.alerts.append(alert)


class MemorySeen:
    def __init__(self):
        self.seen: dict[str, JobPosting] = {}
        self.commits = 0

    def is_seen(self, job):
        return job_key(job) in self.seen

    def seen_urls(self):
        return [j.url for j in self.seen.values()]

    def mark(self, job, score):
        self.seen[job_key(job)] = job

    def commit(self):
        self.commits += 1


class MemoryHistory:
    def __init__(self):
        self.records: list[MatchRecord] = []

    def append(self, record):
        self.records.append(record)
