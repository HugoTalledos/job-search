from datetime import datetime, timedelta, timezone

from job_agent.application import EnsureProfile
from tests.fakes import FakeInferer, FakeRepositories, FakeResume, MemoryProfileStore

START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _setup(profile, refresh_days=30):
    now = [START]
    resume = FakeResume("cv")
    repos = FakeRepositories({"https://g/a": "h1", "https://g/b": "h1"})
    inferer, store = FakeInferer(profile), MemoryProfileStore()
    uc = EnsureProfile(resume, repos, inferer, store, refresh_days=refresh_days, clock=lambda: now[0])
    return uc, now, resume, repos, inferer, store


def test_first_run_builds_and_stores_profile(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    assert uc.execute() == profile
    assert inferer.calls == 1 and repos.collected == 2
    assert store.stored.resume_fingerprint and store.stored.repos_fingerprint
    assert store.stored.repos_checked_at == START


def test_normal_runs_touch_neither_network_nor_llm(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    uc.execute()
    for days in (1, 10, 29):
        now[0] = START + timedelta(days=days)
        uc.execute()
    assert inferer.calls == 1 and repos.listed == 1  # only the first build listed the repos


def test_repo_commits_wait_for_the_interval(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    uc.execute()
    repos.heads["https://g/a"] = "h2"  # a commit
    now[0] = START + timedelta(days=5)
    uc.execute()
    assert inferer.calls == 1  # not yet

    now[0] = START + timedelta(days=30)
    uc.execute()
    assert inferer.calls == 2 and store.stored.built_at == now[0]


def test_unchanged_repos_after_interval_do_not_rebuild_and_are_not_rechecked_every_run(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    uc.execute()
    now[0] = START + timedelta(days=31)
    uc.execute()
    assert inferer.calls == 1 and repos.listed == 2  # checked once...
    assert store.stored.repos_checked_at == now[0]
    now[0] = START + timedelta(days=32)
    uc.execute()
    assert repos.listed == 2  # ...and not again until the next interval


def test_resume_change_rebuilds_right_away(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    uc.execute()
    resume.text = "cv v2"
    now[0] = START + timedelta(hours=1)
    uc.execute()
    assert inferer.calls == 2


def test_force_always_rebuilds(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    uc.execute()
    uc.execute(force=True)
    assert inferer.calls == 2


def test_profile_from_older_version_is_rebuilt_once(profile):
    uc, now, resume, repos, inferer, store = _setup(profile)
    uc.execute()
    store.stored = store.stored.model_copy(update={"resume_fingerprint": "", "repos_fingerprint": ""})
    uc.execute()
    uc.execute()
    assert inferer.calls == 2
