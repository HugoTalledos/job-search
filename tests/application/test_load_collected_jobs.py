from job_agent.application.load_collected_jobs import LoadCollectedJobs


class MemoryPostings:
    def __init__(self, postings):
        self.postings = postings

    def list_postings(self):
        return self.postings


def test_load_collected_jobs_returns_all_postings(job):
    assert LoadCollectedJobs(MemoryPostings([job])).execute() == [job]


def test_load_collected_jobs_accepts_empty_collection():
    assert LoadCollectedJobs(MemoryPostings([])).execute() == []
