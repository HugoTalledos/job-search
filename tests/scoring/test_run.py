import logging

import pytest

from job_agent.scoring.models import PendingPosting, PostingEnrichment, ScoreResult, TelegramMessageRef
from job_agent.scoring.run import ResendPendingNotifications, ScorePendingJobs
from job_agent.scoring.models import PendingNotification


RESULT = ScoreResult(83, 0.7, "typesafe/jev-1.13")


class Profiles:
    def __init__(self, profile, events, error=None):
        self.profile, self.events, self.error = profile, events, error

    def load(self):
        self.events.append("profile")
        if self.error:
            raise self.error
        return self.profile


class Postings:
    def __init__(self, pending, events, error=None):
        self.items = {p.document_id: p for p in pending}
        self.status = {p.document_id: "PENDING" for p in pending}
        self.events, self.error = events, error
        self.fail_stage = None

    def list_pending(self):
        self.events.append("postings")
        if self.error:
            raise self.error
        return [self.items[key] for key, status in self.status.items() if status == "PENDING"]

    def mark_enriched(self, document_id, enrichment):
        self.events.append(f"enriched:{document_id}")
        if self.fail_stage == "enriched" and document_id == "first":
            raise RuntimeError("write failed")
        self.items[document_id] = PendingPosting(document_id, self.items[document_id].job, enrichment)

    def mark_scored(self, document_id, result, notify):
        self.events.append(f"scored:{document_id}:{notify}")
        if self.fail_stage == "scored" and document_id == "first":
            raise RuntimeError("write failed")
        self.status[document_id] = "PENDING_NOTIFICATION" if notify else "EVALUATED"

    def mark_notified(self, document_id):
        self.events.append(f"notified:{document_id}")
        if self.fail_stage == "notified" and document_id == "first":
            raise RuntimeError("write failed")
        self.status[document_id] = "NOTIFIED"


class Scorer:
    def __init__(self, events, result=RESULT):
        self.events, self.result = events, result
        self.error_for = set()

    def score(self, profile, job):
        self.events.append(f"score:{job.external_id}")
        if job.external_id in self.error_for:
            raise RuntimeError("SECRET-JOB-DESCRIPTION")
        return self.result


class Enricher:
    def __init__(self, events, result=PostingEnrichment("English B2", "USD 2,000 - 3,000")):
        self.events, self.result = events, result
        self.error_for = set()

    def enrich(self, job):
        self.events.append(f"enrich:{job.external_id}")
        if job.external_id in self.error_for:
            raise RuntimeError("enrichment failed")
        return self.result


class Notifier:
    def __init__(self, events):
        self.events = events
        self.fail = False

    def notify(self, job, result, enrichment):
        self.events.append(f"notify:{job.external_id}:{result.score}:{enrichment.required_language}")
        if self.fail:
            raise RuntimeError("Telegram unavailable")
        return TelegramMessageRef("42", 91)


class MessageIndex:
    def __init__(self, events):
        self.events = events
        self.fail_for = set()

    def record(self, ref, posting_id):
        self.events.append(f"record:{ref.chat_id}:{ref.message_id}:{posting_id}")
        if posting_id in self.fail_for:
            raise RuntimeError("index unavailable")


def make_run(profile, postings, scorer, enricher, notifier, threshold=70, index=None):
    return ScorePendingJobs(Profiles(profile, postings.events), postings, scorer, enricher, notifier,
                            threshold, index or MessageIndex(postings.events))


def test_empty_collection_does_no_enrichment_score_or_send(profile):
    events = []
    postings = Postings([], events)
    report = make_run(profile, postings, Scorer(events), Enricher(events), Notifier(events)).execute()
    assert events == ["profile", "postings"]
    assert (report.evaluated, report.failed, report.notified) == (0, 0, 0)


def test_high_score_persists_before_sending_and_confirms_notification(profile, job):
    events = []
    postings = Postings([PendingPosting("original", job)], events)
    report = make_run(profile, postings, Scorer(events), Enricher(events), Notifier(events)).execute()

    assert events == [
        "profile", "postings", "enrich:123", "enriched:original", "score:123",
        "scored:original:True", "notify:123:83:English B2", "record:42:91:original", "notified:original",
    ]
    assert postings.status["original"] == "NOTIFIED"
    assert (report.evaluated, report.failed, report.notified) == (1, 0, 1)


def test_threshold_is_inclusive_and_low_offer_is_only_evaluated(profile, job):
    events = []
    postings = Postings([PendingPosting("low", job)], events)
    scorer = Scorer(events, ScoreResult(69, 0.7, "typesafe/jev-1.13"))
    report = make_run(profile, postings, scorer, Enricher(events), Notifier(events)).execute()

    assert postings.status["low"] == "EVALUATED"
    assert report.notified == 0
    assert not any(event.startswith("notify:") for event in events)

    events.clear()
    postings = Postings([PendingPosting("equal", job)], events)
    scorer.result = ScoreResult(70, 0.7, "typesafe/jev-1.13")
    report = make_run(profile, postings, scorer, Enricher(events), Notifier(events)).execute()
    assert postings.status["equal"] == "NOTIFIED"
    assert report.notified == 1


def test_saved_empty_enrichment_skips_jev_extraction(profile, job):
    events = []
    postings = Postings([PendingPosting("saved", job, PostingEnrichment())], events)
    make_run(profile, postings, Scorer(events), Enricher(events), Notifier(events)).execute()
    assert "enrich:123" not in events
    assert "enriched:saved" not in events
    assert "notify:123:83:None" in events


def test_telegram_failure_keeps_pending_notification_and_next_run_does_not_retry(profile, job):
    events = []
    postings = Postings([PendingPosting("offer", job)], events)
    notifier = Notifier(events)
    notifier.fail = True
    run = make_run(profile, postings, Scorer(events), Enricher(events), notifier)

    report = run.execute()
    assert postings.status["offer"] == "PENDING_NOTIFICATION"
    assert (report.evaluated, report.failed, report.notified) == (1, 1, 0)
    events.clear()
    notifier.fail = False
    run.execute()
    assert events == ["profile", "postings"]
    assert postings.status["offer"] == "PENDING_NOTIFICATION"


def test_score_failure_stays_pending_and_next_webhook_reuses_enrichment(profile, job, caplog):
    events = []
    postings = Postings([PendingPosting("offer", job)], events)
    scorer = Scorer(events)
    scorer.error_for.add(job.external_id)
    run = make_run(profile, postings, scorer, Enricher(events), Notifier(events))

    with caplog.at_level(logging.ERROR):
        assert run.execute().failed == 1
    assert postings.status["offer"] == "PENDING"
    assert "SECRET-JOB-DESCRIPTION" not in caplog.text
    events.clear()
    scorer.error_for.clear()
    assert run.execute().notified == 1
    assert "enrich:123" not in events
    assert postings.status["offer"] == "NOTIFIED"


@pytest.mark.parametrize("stage", ["enrichment", "enriched", "scored", "notified"])
def test_stage_failure_preserves_state_and_continues_other_offers(profile, job, stage):
    events = []
    other = job.model_copy(update={"external_id": "124"})
    postings = Postings([PendingPosting("first", job), PendingPosting("second", other)], events)
    scorer = Scorer(events)
    enricher = Enricher(events)
    notifier = Notifier(events)
    if stage == "enrichment":
        enricher.error_for.add(job.external_id)
    else:
        postings.fail_stage = stage

    report = make_run(profile, postings, scorer, enricher, notifier).execute()

    assert report.failed >= 1
    assert postings.status["first"] == ("PENDING_NOTIFICATION" if stage == "notified" else "PENDING")
    assert "enrich:124" in events
    assert postings.status["second"] == "NOTIFIED"
    if stage == "scored":
        assert "notify:123:83:English B2" not in events


@pytest.mark.parametrize("source", ["profile", "postings"])
def test_input_failure_aborts_before_work(profile, source):
    events = []
    profiles = Profiles(profile, events, RuntimeError("profile unavailable") if source == "profile" else None)
    postings = Postings([], events, RuntimeError("postings unavailable") if source == "postings" else None)

    with pytest.raises(RuntimeError, match="unavailable"):
        ScorePendingJobs(profiles, postings, Scorer(events), Enricher(events), Notifier(events), 70, MessageIndex(events)).execute()

    assert not any(event.startswith(("enrich:", "score:", "notify:")) for event in events)


def test_resend_uses_saved_result_and_enrichment_and_marks_only_successes(job):
    events = []
    other = job.model_copy(update={"external_id": "124"})
    saved = [
        PendingNotification("first", job, RESULT, PostingEnrichment("English B2", "$2000")),
        PendingNotification("second", other, RESULT, PostingEnrichment()),
    ]

    class PendingStore:
        def list_pending_notifications(self):
            events.append("list")
            return saved

        def mark_notified(self, document_id):
            events.append(f"notified:{document_id}")

    class FailingNotifier:
        def notify(self, posting, result, enrichment):
            events.append(f"notify:{posting.external_id}:{result.score}:{enrichment.required_language}")
            if posting.external_id == "123":
                raise RuntimeError("Telegram unavailable")
            return TelegramMessageRef("42", 91)

    report = ResendPendingNotifications(PendingStore(), FailingNotifier(), MessageIndex(events)).execute()

    assert events == ["list", "notify:123:83:English B2", "notify:124:83:None", "record:42:91:second", "notified:second"]
    assert (report.pending, report.notified, report.failed) == (2, 1, 1)


def test_resend_with_no_pending_notifications_does_not_send():
    class EmptyStore:
        def list_pending_notifications(self):
            return []

    class NoSend:
        def notify(self, *args):
            raise AssertionError("unexpected send")

    report = ResendPendingNotifications(EmptyStore(), NoSend(), MessageIndex([])).execute()
    assert (report.pending, report.notified, report.failed) == (0, 0, 0)


def test_failed_message_record_keeps_pending_notification_and_continues(profile, job):
    events = []
    other = job.model_copy(update={"external_id": "124"})
    postings = Postings([PendingPosting("first", job), PendingPosting("second", other)], events)
    index = MessageIndex(events)
    index.fail_for.add("first")

    report = make_run(profile, postings, Scorer(events), Enricher(events), Notifier(events), index=index).execute()

    assert postings.status == {"first": "PENDING_NOTIFICATION", "second": "NOTIFIED"}
    assert "notified:first" not in events
    assert events[-3:] == ["notify:124:83:English B2", "record:42:91:second", "notified:second"]
    assert (report.evaluated, report.failed, report.notified) == (2, 1, 1)


def test_resend_records_message_before_notified(job):
    events = []
    pending = PendingNotification("offer", job, RESULT, PostingEnrichment())

    class Store:
        status = "PENDING_NOTIFICATION"

        def list_pending_notifications(self):
            return [pending]

        def mark_notified(self, document_id):
            events.append(f"notified:{document_id}")
            self.status = "NOTIFIED"

    class Sender:
        def notify(self, job, result, enrichment):
            events.append("send:offer")
            return TelegramMessageRef("42", 91)

    store = Store()
    report = ResendPendingNotifications(store, Sender(), MessageIndex(events)).execute()
    assert events == ["send:offer", "record:42:91:offer", "notified:offer"]
    assert store.status == "NOTIFIED"
    assert (report.pending, report.notified, report.failed) == (1, 1, 0)


def test_failed_resend_record_leaves_pending_notification(job):
    events = []
    pending = PendingNotification("offer", job, RESULT, PostingEnrichment())

    class Store:
        status = "PENDING_NOTIFICATION"

        def list_pending_notifications(self):
            return [pending]

        def mark_notified(self, document_id):
            self.status = "NOTIFIED"

    store = Store()
    index = MessageIndex(events)
    index.fail_for.add("offer")
    report = ResendPendingNotifications(store, Notifier(events), index).execute()
    assert events == ["notify:123:83:None", "record:42:91:offer"]
    assert store.status == "PENDING_NOTIFICATION"
    assert (report.pending, report.notified, report.failed) == (1, 0, 1)
