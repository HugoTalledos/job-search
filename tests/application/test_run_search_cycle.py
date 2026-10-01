from job_agent.application import RunSearchCycle
from job_agent.domain.policies import MatchingPolicy, SearchPreferences
from tests.fakes import (
    FakeResume,
    FakeSource,
    FakeTailor,
    MemoryApplications,
    MemoryHistory,
    MemorySeen,
    RecordingNotifier,
    StaticProfile,
    TableMatcher,
)


def _cycle(profile, sources, matcher, tailor, **prefs):
    deps = dict(
        notifier=RecordingNotifier(), seen=MemorySeen(), history=MemoryHistory(), applications=MemoryApplications()
    )
    uc = RunSearchCycle(
        ensure_profile=StaticProfile(profile),
        resume=FakeResume(),
        sources=sources,
        matcher=matcher,
        tailor=tailor,
        preferences=SearchPreferences(**prefs),
        policy=MatchingPolicy(70, 70),
        **deps,
    )
    return uc, deps


def test_full_cycle(profile, job, match, tailored):
    good_fit = job.model_copy(update={"external_id": "2", "title": "Data Engineer", "url": "https://x/2"})
    weak = job.model_copy(update={"external_id": "3", "title": "Chef", "url": "https://x/3"})
    excluded = job.model_copy(update={"external_id": "4", "title": "SRE", "company": "Evil", "url": "https://x/4"})
    matcher = TableMatcher({
        job.title: match,                                                   # notify + tailor
        good_fit.title: match.model_copy(update={"resume_undersells": False}),  # notify only
        weak.title: match.model_copy(update={"score": 20, "verdict": "no"}),     # silent
    })
    linkedin = FakeSource("linkedin", [job, good_fit, job])  # duplicate within the run
    other = FakeSource("other", [weak, excluded])
    broken = FakeSource("broken", error=RuntimeError("session expired"))
    tailor = FakeTailor(tailored)
    uc, deps = _cycle(profile, [linkedin, broken, other], matcher, tailor, exclude_companies=("evil",))

    report = uc.execute()

    assert (report.found, report.new, report.scored, report.notified, report.tailored) == (5, 3, 3, 2, 1)
    assert report.errors == ["source broken: session expired"]
    assert linkedin.criteria.target_roles == profile.target_roles
    alerts = deps["notifier"].alerts
    assert [a.job.title for a in alerts] == [job.title, good_fit.title]
    assert alerts[0].tailored == tailored and alerts[0].application.link.startswith("https://repo/")
    assert alerts[1].tailored is None and tailor.calls == [job]
    assert [r.score for r in deps["history"].records] == [82, 82, 20]

    # Next cycle: everything already seen.
    report = uc.execute()
    assert report.new == 0 and len(deps["notifier"].alerts) == 2
    assert set(linkedin.criteria.already_seen_urls) == {job.url, good_fit.url, weak.url}


def test_scoring_and_tailoring_failures_are_isolated(profile, job, match, tailored):
    other = job.model_copy(update={"external_id": "2", "title": "Other", "url": "https://x/2"})

    class FlakyMatcher:
        def score(self, j, p, r):
            if j.title == "Other":
                raise ValueError("boom")
            return match

    class BrokenTailor:
        def tailor(self, *a):
            raise RuntimeError("refused")

    uc, deps = _cycle(profile, [FakeSource("s", [other, job])], FlakyMatcher(), BrokenTailor())
    report = uc.execute()
    assert report.scored == 1 and report.notified == 1 and report.tailored == 0
    assert len(report.errors) == 2
    assert deps["notifier"].alerts[0].tailored is None  # still notified without a tailored resume
    assert not deps["seen"].is_seen(other)  # unscored posting is retried next cycle


def test_max_jobs_per_run(profile, job, match, tailored):
    jobs = [job.model_copy(update={"external_id": str(i), "title": f"T{i}", "url": f"https://x/{i}"}) for i in range(5)]
    uc, deps = _cycle(profile, [FakeSource("s", jobs)], TableMatcher({j.title: match for j in jobs}),
                      FakeTailor(tailored), max_jobs_per_run=2)
    assert uc.execute().scored == 2
