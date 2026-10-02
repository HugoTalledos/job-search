import json
import sys
from pathlib import Path

from job_agent.adapters.job_sources import LinkedInMcpJobSource, McpServerParams
from job_agent.application.ports import JobSource
from job_agent.domain.models import SearchPlan, SearchQuery
from job_agent.domain.policies import lead_key


def _source(tmp_path, slow_id="", tool_timeout=200.0):
    log = tmp_path / "calls.jsonl"
    server = McpServerParams(
        name="linkedin", command=sys.executable, args=[str(Path(__file__).parent / "fake_jobs_mcp.py")],
        env={"FAKE_MCP_LOG": str(log), "FAKE_MCP_SLOW_ID": slow_id},
    )
    return LinkedInMcpJobSource(server, max_pages=1, tool_timeout=tool_timeout), log


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


def test_stuck_detail_is_skipped_after_timeout(tmp_path, caplog):
    import time

    source, log = _source(tmp_path, slow_id="101", tool_timeout=2)
    plan = SearchPlan(queries=[SearchQuery(keywords="x", location="Colombia")], posted_within_days=1)
    started = time.monotonic()
    with caplog.at_level("INFO"):
        postings = source.collect(plan, lambda lead: True, max_details=10)
    assert [p.external_id for p in postings] == ["102"]  # 101 timed out, the run went on
    assert time.monotonic() - started < 20
    assert "detail 1/2 (101) failed" in caplog.text and "detail 2/2 in" in caplog.text


def test_collect_new_checks_known_ids_once_before_using_detail_budget(tmp_path):
    source, log = _source(tmp_path)
    plan = SearchPlan(queries=[SearchQuery(keywords="backend", location="Colombia"),
                               SearchQuery(keywords="backend", location="Remote")], posted_within_days=1)
    batches = []

    def known_keys(leads):
        batches.append([lead.external_id for lead in leads])
        return {lead_key(lead) for lead in leads if lead.external_id == "101"}

    result = source.collect_new(plan, known_keys, max_details=1)

    assert batches == [["101", "102", "103"]]
    assert (result.leads, result.known) == (3, 1)
    assert [job.external_id for job in result.jobs] == ["102"]
    assert [call["job_id"] for call in _calls(log) if call["tool"] == "get_job_details"] == ["102"]


def test_collect_new_reports_detail_failures_and_continues(tmp_path):
    source, log = _source(tmp_path, slow_id="101", tool_timeout=2)
    plan = SearchPlan(queries=[SearchQuery(keywords="backend", location="Colombia")], posted_within_days=1)

    result = source.collect_new(plan, lambda leads: set(), max_details=2)

    assert [job.external_id for job in result.jobs] == ["102"]
    assert len(result.detail_errors) == 1
    assert "101" in result.detail_errors[0]
    assert [call["job_id"] for call in _calls(log) if call["tool"] == "get_job_details"] == ["101", "102"]
