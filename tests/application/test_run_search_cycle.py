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
    ScriptedSelector,
    StaticProfile,
    TableMatcher,
)


def _cycle(profile, sources, matcher, tailor, selector=None, **prefs):
    deps = dict(
        notifier=RecordingNotifier(), seen=MemorySeen(), history=MemoryHistory(), applications=MemoryApplications()
    )
    uc = RunSearchCycle(
        ensure_profile=StaticProfile(profile),
        resume=FakeResume(),
        sources=sources,
        matcher=matcher,
        tailor=tailor,
        selector=selector or ScriptedSelector("create"),
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


def _similar(job, n):
    return job.model_copy(update={"external_id": f"s{n}", "title": f"Backend Engineer {n}", "url": f"https://x/s{n}"})


def _two_cycles(profile, job, match, tailored, selector, resume=None):
    """First cycle creates a version for ``job``; the second sees a similar posting."""
    second = _similar(job, 2)
    source = FakeSource("s", [job])
    tailor = FakeTailor(tailored)
    uc, deps = _cycle(profile, [source], TableMatcher({job.title: match, second.title: match}), tailor, selector)
    first = uc.execute()
    assert first.tailored == 1 and selector.offered == []  # empty library: no selector call
    source.jobs = [second]
    if resume is not None:
        uc.resume = resume
    return uc.execute(), deps, tailor, second


def test_reuses_matching_version(profile, job, match, tailored):
    selector = ScriptedSelector("reuse")
    report, deps, tailor, second = _two_cycles(profile, job, match, tailored, selector)
    assert (report.reused, report.adapted, report.tailored) == (1, 0, 0)
    assert len(tailor.calls) == 1  # no new tailoring
    alert = deps["notifier"].alerts[-1]
    assert alert.resume_origin == "reused" and alert.tailored is None
    assert alert.source_version.job_title == job.title and alert.reuse_rationale == "motivo"
    [version] = deps["applications"].list_versions()
    assert [u.title for u in version.used_for] == [job.title, second.title]
    assert deps["history"].records[-1].resume_origin == "reused"


def test_adapts_closest_version(profile, job, match, tailored):
    selector = ScriptedSelector("adapt")
    report, deps, tailor, second = _two_cycles(profile, job, match, tailored, selector)
    assert (report.reused, report.adapted, report.tailored) == (0, 1, 0)
    assert tailor.starting_from == [None, tailored.resume_markdown]
    versions = {v.job_title: v for v in deps["applications"].list_versions()}
    assert versions[second.title].adapted_from == versions[job.title].id
    assert deps["notifier"].alerts[-1].resume_origin == "adapted"


def test_creates_when_selector_says_so_or_picks_unknown_version(profile, job, match, tailored):
    for selector in (ScriptedSelector("create"), ScriptedSelector("reuse", version_id="does-not-exist")):
        report, deps, tailor, _ = _two_cycles(profile, job, match, tailored, selector)
        assert (report.reused, report.adapted, report.tailored) == (0, 0, 1)
        assert tailor.starting_from == [None, None]


def test_versions_from_an_older_base_resume_are_not_offered(profile, job, match, tailored):
    selector = ScriptedSelector("reuse")
    report, deps, tailor, _ = _two_cycles(profile, job, match, tailored, selector, resume=FakeResume("# CV v2"))
    assert selector.offered == []  # old version invalidated -> straight to create
    assert report.tailored == 1 and report.reused == 0
