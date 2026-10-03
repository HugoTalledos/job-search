from datetime import datetime, timezone

import pytest

from job_agent.application import BuildProfessionalProfile, EnsureProfile
from job_agent.domain.models import Skill, StoredProfile
from tests.fakes import FakeInferer, FakeRepositories, FakeResume, MemoryProfileStore


class Reporter:
    def __init__(self):
        self.built_calls = []
        self.failures = 0

    def built(self, profile, changes, first_build):
        self.built_calls.append((profile, changes, first_build))

    def failed(self):
        self.failures += 1


def _use_case(profile, store, inferer=None):
    reporter = Reporter()
    ensure = EnsureProfile(FakeResume("cv"), FakeRepositories({"https://g/a": "h1"}), inferer or FakeInferer(profile), store)
    return BuildProfessionalProfile(ensure, store, reporter), reporter


def test_first_build_stores_profile_and_reports_it(profile):
    store = MemoryProfileStore()
    use_case, reporter = _use_case(profile, store)

    assert use_case.execute() == profile
    assert store.stored.profile == profile
    assert reporter.built_calls == [(profile, [], True)]


def test_rebuild_is_forced_and_reports_changes(profile):
    store = MemoryProfileStore()
    store.save(StoredProfile(profile=profile, resume_fingerprint="x", built_at=datetime.now(timezone.utc)))
    rebuilt = profile.model_copy(update={"skills": [*profile.skills, Skill(name="Go", level="basic", evidence="r")]})
    inferer = FakeInferer(rebuilt)
    use_case, reporter = _use_case(rebuilt, store, inferer)

    use_case.execute()

    assert inferer.calls == 1
    assert store.stored.profile == rebuilt
    [(_, changes, first_build)] = reporter.built_calls
    assert first_build is False
    assert changes == ["Habilidades nuevas: Go (básico)"]


def test_failure_is_reported_and_raised(profile):
    class BrokenInferer:
        def infer(self, *args):
            raise RuntimeError("llm down")

    store = MemoryProfileStore()
    use_case, reporter = _use_case(profile, store, BrokenInferer())

    with pytest.raises(RuntimeError):
        use_case.execute()
    assert reporter.failures == 1 and reporter.built_calls == []
    assert store.stored is None
