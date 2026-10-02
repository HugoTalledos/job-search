import json
import sys
from pathlib import Path

from job_agent.adapters.job_sources import LinkedInMcpJobSource, McpServerParams
from job_agent.application.ports import JobSource
from job_agent.domain.models import SearchPlan, SearchQuery


def _source(tmp_path):
    log = tmp_path / "calls.jsonl"
    server = McpServerParams(
        name="linkedin", command=sys.executable, args=[str(Path(__file__).parent / "fake_jobs_mcp.py")],
        env={"FAKE_MCP_LOG": str(log)},
    )
    return LinkedInMcpJobSource(server, max_pages=1), log


def _calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_searches_with_native_filters_and_fetches_only_admitted_ids(tmp_path):
    source, log = _source(tmp_path)
    assert isinstance(source, JobSource)
    plan = SearchPlan(
        queries=[SearchQuery(keywords="backend", location="Colombia"), SearchQuery(keywords="backend", location="Remote"),
                 SearchQuery(keywords="boom", location="Remote")],
        posted_within_days=1, work_types=["remote", "hybrid"],
    )
    asked = []

    def admit(lead):
        asked.append(lead.external_id)
        return lead.external_id != "102"  # pretend 102 was processed in an earlier run

    postings = source.collect(plan, admit, max_details=10)

    assert asked == ["101", "102", "103"]  # each unique id asked once, in order
    assert [p.external_id for p in postings] == ["101", "103"]
    calls = _calls(log)
    searches = [c for c in calls if c["tool"] == "search_jobs"]
    assert len(searches) == 3  # the failing query did not stop the run
    assert searches[0] == {"tool": "search_jobs", "keywords": "backend", "location": "Colombia", "date_posted": "r86400",
                           "work_type": "remote,hybrid", "sort_by": "date", "max_pages": 1}
    assert [c["job_id"] for c in calls if c["tool"] == "get_job_details"] == ["101", "103"]  # never 102

    first = postings[0]
    assert (first.title, first.company, first.location, first.remote) == (
        "Sr. Backend Engineer", "Acme Corp", "Bogotá, Colombia", "remote")
    assert first.description == "Python, FastAPI and AWS."
    assert first.url == "https://www.linkedin.com/jobs/view/101/"


def test_details_budget(tmp_path):
    source, log = _source(tmp_path)
    plan = SearchPlan(queries=[SearchQuery(keywords="x", location="Colombia")], posted_within_days=3)
    assert len(source.collect(plan, lambda lead: True, max_details=1)) == 1
    assert [c["date_posted"] for c in _calls(log) if c["tool"] == "search_jobs"] == ["r259200"]
