from datetime import datetime, timedelta, timezone

from job_agent.application import EnsureProfile
from tests.fakes import FakeInferer, FakeRepositories, FakeResume, MemoryProfileStore


def _use_case(profile, now, heads=None, resume="cv"):
    repos = FakeRepositories(heads or {"https://g/a": "h1"})
    inferer, store = FakeInferer(profile), MemoryProfileStore()
    uc = EnsureProfile(FakeResume(resume), repos, inferer, store, refresh_days=7, clock=lambda: now[0])
    return uc, repos, inferer, store


def test_infers_once_then_reuses_until_inputs_change(profile):
    now = [datetime(2026, 10, 1, tzinfo=timezone.utc)]
    uc, repos, inferer, store = _use_case(profile, now)
    assert uc.execute() == profile and inferer.calls == 1
    assert store.stored.repositories[0].summary == "repo https://g/a"
    uc.execute()
    assert inferer.calls == 1  # cached

    repos.heads["https://g/a"] = "h2"  # new commit
    uc.execute()
    assert inferer.calls == 2

    now[0] += timedelta(days=8)  # stale
    uc.execute()
    assert inferer.calls == 3

    uc.execute(force=True)
    assert inferer.calls == 4
