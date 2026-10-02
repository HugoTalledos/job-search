"""In-memory implementations of the application ports, for use-case tests."""

from __future__ import annotations

from datetime import datetime, timezone

from job_agent.domain.models import (
    JobAlert,
    JobLead,
    JobMatch,
    JobPosting,
    MatchRecord,
    Profile,
    RepoEvidence,
    RepoRef,
    ResumeUse,
    ResumeVersion,
    ReuseDecision,
    SavedApplication,
    SearchPlan,
    StoredProfile,
    TailoredResume,
)
from job_agent.domain.policies import duplicate_signature, job_key


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
    """Returns fixed postings, asking ``admit`` about each one like a real source does with its ids."""

    def __init__(self, name, jobs=None, error=None):
        self.name = name
        self.jobs = jobs or []
        self.error = error
        self.plan: SearchPlan | None = None
        self.fetched: list[JobPosting] = []

    def collect(self, plan, admit, max_details):
        self.plan = plan
        if self.error:
            raise self.error
        out = []
        for job in self.jobs:
            if len(out) >= max_details:
                break
            if admit(JobLead(source=job.source, external_id=job.external_id, url=job.url)):
                out.append(job)
        self.fetched += out
        return out


class TableMatcher:
    def __init__(self, scores: dict[str, JobMatch]):
        self.scores = scores

    def score(self, job, profile, resume_text):
        return self.scores[job.title]


class FakeTailor:
    def __init__(self, tailored: TailoredResume):
        self.tailored = tailored
        self.calls: list[JobPosting] = []
        self.starting_from: list[str | None] = []

    def tailor(self, job, match, profile, resume_text, starting_from=None):
        self.calls.append(job)
        self.starting_from.append(starting_from)
        return self.tailored


class ScriptedSelector:
    """Returns a fixed decision; ``version_id='*'`` means the first candidate offered."""

    def __init__(self, action="create", version_id="*"):
        self.action, self.version_id = action, version_id
        self.offered: list[list[ResumeVersion]] = []

    def choose(self, job, match, candidates):
        self.offered.append(candidates)
        vid = candidates[0].id if self.version_id == "*" and self.action != "create" else self.version_id
        return ReuseDecision(action=self.action, version_id=vid if self.action != "create" else "",
                             covered_requirements=[], missing_requirements=[], rationale="motivo")


class MemoryApplications:
    def __init__(self):
        self.versions: dict[str, ResumeVersion] = {}
        self.markdown: dict[str, str] = {}

    def save(self, job, match, tailored, base_fingerprint, adapted_from=None):
        vid = f"output/{job_key(job)}"
        now = datetime.now(timezone.utc)
        self.versions[vid] = ResumeVersion(
            id=vid, job_key=job_key(job), job_title=job.title, company=job.company, language=tailored.language,
            highlights=tailored.highlights, base_fingerprint=base_fingerprint, created_at=now,
            adapted_from=adapted_from, used_for=[ResumeUse(job_key=job_key(job), title=job.title, company=job.company, at=now)],
        )
        self.markdown[vid] = tailored.resume_markdown
        return self.locate(vid)

    def list_versions(self):
        return list(self.versions.values())

    def load_markdown(self, version_id):
        return self.markdown[version_id]

    def locate(self, version_id):
        return SavedApplication(version_id=version_id, folder=version_id, link=f"https://repo/{version_id}")

    def record_use(self, version_id, job):
        self.versions[version_id].used_for.append(
            ResumeUse(job_key=job_key(job), title=job.title, company=job.company, at=datetime.now(timezone.utc))
        )


class RecordingNotifier:
    def __init__(self):
        self.alerts: list[JobAlert] = []

    def notify(self, alert):
        self.alerts.append(alert)


class MemorySeen:
    def __init__(self):
        self.seen: dict[str, tuple[JobPosting, str]] = {}
        self.commits = 0

    def is_seen_key(self, key):
        return key in self.seen

    def is_duplicate(self, job):
        sig = duplicate_signature(job)
        return bool(sig) and any(k != job_key(job) and duplicate_signature(j) == sig for k, (j, _) in self.seen.items())

    def mark(self, job, outcome, score=None):
        self.seen[job_key(job)] = (job, outcome)

    def commit(self):
        self.commits += 1


class MemoryHistory:
    def __init__(self):
        self.records: list[MatchRecord] = []

    def append(self, record):
        self.records.append(record)
