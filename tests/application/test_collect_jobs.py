import pytest

from job_agent.application.collect_jobs import CollectJobs
from job_agent.domain.models import CollectorPlan, JobLead, SearchPlan, SearchQuery, SourceCollection
from job_agent.domain.policies import job_key, lead_key


class MemoryStore:
    def __init__(self, plan):
        self.plan = plan
        self.jobs = {}
        self.fail_on = None
        self.events = []

    def load_plan(self):
        self.events.append("plan")
        if self.plan is None:
            raise ValueError("No search plan")
        return self.plan

    def known_keys(self, leads):
        return {lead_key(lead) for lead in leads if lead_key(lead) in self.jobs}

    def save(self, job):
        if job.external_id == self.fail_on:
            raise RuntimeError("Firestore unavailable")
        key = job_key(job)
        if key in self.jobs:
            return False
        self.jobs[key] = job
        return True


class MemorySource:
    def __init__(self, jobs, store):
        self.jobs = jobs
        self.store = store
        self.fetched = []

    def collect_new(self, plan, known_keys, max_details):
        self.store.events.append("source")
        leads = [JobLead(source=j.source, external_id=j.external_id, url=j.url) for j in self.jobs]
        known = known_keys(leads)
        fresh = [j for j in self.jobs if job_key(j) not in known][:max_details]
        self.fetched += [j.external_id for j in fresh]
        return SourceCollection(jobs=fresh, leads=len(leads), known=len(known))


class FailedSearchSource:
    def collect_new(self, plan, known_keys, max_details):
        return SourceCollection(jobs=[], leads=0, known=0, search_errors=["boom: rate limited"])


def _plan(max_details=2):
    return CollectorPlan(search=SearchPlan(queries=[SearchQuery(keywords="backend")], posted_within_days=2),
                         max_details_per_run=max_details)


def test_collector_reads_plan_before_source_and_skips_known_ids(job):
    store = MemoryStore(_plan(max_details=1))
    known = job.model_copy(update={"external_id": "101"})
    new = job.model_copy(update={"external_id": "102"})
    store.jobs[job_key(known)] = known
    source = MemorySource([known, new], store)

    report = CollectJobs(source, store).execute()

    assert store.events == ["plan", "source"]
    assert source.fetched == ["102"]
    assert (report.leads, report.known, report.fetched, report.inserted) == (2, 1, 1, 1)


def test_collector_repeated_run_does_not_duplicate_postings(job):
    store = MemoryStore(_plan())
    source = MemorySource([job], store)
    collector = CollectJobs(source, store)

    assert collector.execute().inserted == 1
    assert collector.execute().inserted == 0
    assert len(store.jobs) == 1
    assert source.fetched == [job.external_id]


def test_collector_reports_save_failure_after_other_posting_was_saved(job):
    store = MemoryStore(_plan())
    second = job.model_copy(update={"external_id": "broken"})
    store.fail_on = "broken"
    source = MemorySource([job, second], store)

    report = CollectJobs(source, store).execute()

    assert report.inserted == 1
    assert len(report.errors) == 1 and "broken" in report.errors[0]
    assert list(store.jobs) == [job_key(job)]


def test_collector_does_not_open_source_without_search_plan(job):
    store = MemoryStore(None)
    source = MemorySource([job], store)

    with pytest.raises(ValueError, match="search plan"):
        CollectJobs(source, store).execute()

    assert store.events == ["plan"]


def test_collector_reports_search_failure_instead_of_success():
    report = CollectJobs(FailedSearchSource(), MemoryStore(_plan())).execute()

    assert report.inserted == 0
    assert report.errors == ["boom: rate limited"]
