"""Minimal stdio MCP server mimicking the LinkedIn MCP server's job tools (same names and arguments)."""

import json
import os
import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-linkedin")
LOG = os.environ.get("FAKE_MCP_LOG")

POSTINGS = {
    "101": "Acme Corp\nShare\nShow more options\nSr. Backend Engineer\nBogotá, Colombia · 3 hours ago · 12 applicants\n"
           "Remote\nFull-time\nEasy Apply\nSave\nAbout the job\nPython, FastAPI and AWS.",
    "102": "Globex\nData Engineer\nMedellín, Colombia · 1 day ago · Over 100 applicants\nHybrid\n"
           "About the job\nSpark and Airflow.",
    "103": "Initech\nQA Analyst\nRemote · 2 days ago\nAbout the job\nManual testing.",
}


def _log(entry):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps(entry) + "\n")


@server.tool()
def search_jobs(keywords: str, location: str | None = None, max_pages: int = 3, date_posted: str | None = None,
                job_type: str | None = None, experience_level: str | None = None, work_type: str | None = None,
                easy_apply: bool = False, sort_by: str | None = None) -> dict:
    """Search for jobs on LinkedIn."""
    _log({"tool": "search_jobs", "keywords": keywords, "location": location, "date_posted": date_posted,
          "work_type": work_type, "sort_by": sort_by, "max_pages": max_pages})
    if keywords == "boom":
        raise ValueError("rate limited")
    ids = ["101", "102"] if location == "Colombia" else ["102", "103"]
    return {"url": "https://www.linkedin.com/jobs/search/", "sections": {"search_results": "..."}, "job_ids": ids}


@server.tool()
async def get_job_details(job_id: str) -> dict:
    """Get job details for a specific job posting on LinkedIn."""
    _log({"tool": "get_job_details", "job_id": job_id})
    if job_id == os.environ.get("FAKE_MCP_SLOW_ID"):
        import anyio

        await anyio.sleep(30)  # a page that never finishes loading
    return {"url": f"https://www.linkedin.com/jobs/view/{job_id}/", "sections": {"job_posting": POSTINGS[job_id]}}


if __name__ == "__main__":
    server.run()
    sys.exit(0)
