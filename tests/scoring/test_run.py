import logging

import pytest

from job_agent.scoring.models import PendingPosting, ScoreResult
from job_agent.scoring.run import ScorePendingJobs


RESULT = ScoreResult(score=83, confidence=0.7, model="typesafe/jev-1.13")


class Profiles:
    def __init__(self, profile, events, error=None):
        self.profile = profile
        self.events = events
        self.error = error

    def load(self):
        self.events.append("profile")
        if self.error:
            raise self.error
        return self.profile


class Postings:
    def __init__(self, pending, events, error=None):
        self.pending = {p.document_id: p for p in pending}
        self.events = events
        self.error = error
        self.write_error_for = set()
        self.saved = []

    def list_pending(self):
        self.events.append("postings")
        if self.error:
            raise self.error
        return list(self.pending.values())

    def mark_evaluated(self, document_id, result):
        if document_id in self.write_error_for:
            raise RuntimeError("write failed")
        self.saved.append((document_id, result))
        del self.pending[document_id]


class Scorer:
    def __init__(self):
        self.calls = []
        self.fail_for = set()

    def score(self, profile, job):
        self.calls.append((profile, job))
        if job.external_id in self.fail_for:
            raise RuntimeError("SECRET-JOB-DESCRIPTION")
        return RESULT


def test_run_reads_profile_before_postings_and_skips_empty_collection(profile):
    events = []
    scorer = Scorer()

    report = ScorePendingJobs(Profiles(profile, events), Postings([], events), scorer).execute()

    assert events == ["profile", "postings"]
    assert scorer.calls == []
    assert (report.evaluated, report.failed) == (0, 0)


def test_run_saves_score_under_original_document_id(profile, job):
    events = []
    postings = Postings([PendingPosting("original-id", job)], events)
    scorer = Scorer()

    report = ScorePendingJobs(Profiles(profile, events), postings, scorer).execute()

    assert scorer.calls == [(profile, job)]
    assert postings.saved == [("original-id", RESULT)]
    assert (report.evaluated, report.failed) == (1, 0)


def test_scorer_failure_leaves_posting_for_retry_and_continues(profile, job, caplog):
    other = job.model_copy(update={"external_id": "124"})
    postings = Postings([PendingPosting("first", job), PendingPosting("second", other)], [])
    scorer = Scorer()
    scorer.fail_for.add(job.external_id)
    run = ScorePendingJobs(Profiles(profile, []), postings, scorer)

    with caplog.at_level(logging.ERROR):
        report = run.execute()

    assert (report.evaluated, report.failed) == (1, 1)
    assert postings.saved == [("second", RESULT)]
    assert "first" in postings.pending
    assert "first" in caplog.text
    assert "SECRET-JOB-DESCRIPTION" not in caplog.text

    scorer.fail_for.clear()
    assert run.execute().evaluated == 1
    assert postings.pending == {}


def test_write_failure_leaves_posting_for_retry_and_continues(profile, job):
    other = job.model_copy(update={"external_id": "124"})
    postings = Postings([PendingPosting("first", job), PendingPosting("second", other)], [])
    postings.write_error_for.add("first")
    run = ScorePendingJobs(Profiles(profile, []), postings, Scorer())

    report = run.execute()

    assert (report.evaluated, report.failed) == (1, 1)
    assert "first" in postings.pending
    assert postings.saved == [("second", RESULT)]
    postings.write_error_for.clear()
    assert run.execute().evaluated == 1
    assert postings.pending == {}


@pytest.mark.parametrize("source", ["profile", "postings"])
def test_input_failure_aborts_before_scoring(profile, source):
    events = []
    profiles = Profiles(profile, events, RuntimeError("profile unavailable") if source == "profile" else None)
    postings = Postings([], events, RuntimeError("postings unavailable") if source == "postings" else None)
    scorer = Scorer()

    with pytest.raises(RuntimeError, match="unavailable"):
        ScorePendingJobs(profiles, postings, scorer).execute()

    assert scorer.calls == []
