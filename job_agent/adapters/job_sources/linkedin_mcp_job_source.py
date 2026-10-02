"""JobSource port for LinkedIn through its MCP server (stickerdaniel/linkedin-mcp-server), without any LLM.

The adapter calls the server's tools directly:
- ``search_jobs`` for every query of the plan, with LinkedIn's own filters (date posted, work type,
  experience level, sorted by date) -> job ids;
- ``get_job_details`` only for ids the application admits (not processed before), up to the budget.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ...domain.models import JobLead, JobPosting, SearchPlan, SourceCollection
from ...domain.policies import lead_key
from .linkedin_text import parse_job_posting

log = logging.getLogger(__name__)

_INHERITED_ENV = ("PATH", "HOME", "USER", "LANG", "TMPDIR", "XDG_CACHE_HOME", "UV_CACHE_DIR",
                  "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


@dataclass(frozen=True)
class McpServerParams:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def stdio(self) -> StdioServerParameters:
        inherited = {k: v for k, v in os.environ.items() if k in _INHERITED_ENV}
        return StdioServerParameters(command=self.command, args=self.args, env={**inherited, **self.env})


async def open_mcp_session(stack: AsyncExitStack, server: McpServerParams) -> ClientSession:
    read, write = await stack.enter_async_context(stdio_client(server.stdio()))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


class McpToolError(RuntimeError):
    pass


#: The LinkedIn server gives each tool call up to 180 s; slightly more here so a stuck page cannot hang the run.
TOOL_TIMEOUT_SECONDS = 200.0


async def call_tool(session: ClientSession, name: str, arguments: dict, timeout: float = TOOL_TIMEOUT_SECONDS) -> dict:
    """Call an MCP tool and return its JSON result (structured content, or the JSON text block)."""
    try:
        result = await session.call_tool(name, arguments, read_timeout_seconds=timeout)
    except Exception as exc:  # timeouts and transport errors: report like a tool error
        raise McpToolError(f"{name} failed: {type(exc).__name__}: {exc}"[:300]) from exc
    text = "\n".join(getattr(block, "text", "") for block in result.content)
    if result.is_error:
        raise McpToolError(f"{name} failed: {text[:300]}")
    data = result.structured_content
    if data is None:
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise McpToolError(f"{name} returned non-JSON content") from exc
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data if isinstance(data, dict) else {}


def linkedin_date_filter(days: int) -> str:
    """LinkedIn's f_TPR value (seconds), accepted as-is by the server's ``date_posted``."""
    return f"r{max(1, days) * 86400}"


class LinkedInMcpJobSource:
    name = "linkedin"

    def __init__(self, server: McpServerParams, max_pages: int = 2, tool_timeout: float = TOOL_TIMEOUT_SECONDS) -> None:
        self.server = server
        self.max_pages = max_pages
        self.tool_timeout = tool_timeout

    def collect(self, plan: SearchPlan, admit: Callable[[JobLead], bool], max_details: int) -> list[JobPosting]:
        return asyncio.run(self._collect(plan, admit, max_details))

    def collect_new(
        self, plan: SearchPlan, known_keys: Callable[[list[JobLead]], set[str]], max_details: int,
    ) -> SourceCollection:
        return asyncio.run(self._collect_new(plan, known_keys, max_details))

    async def _collect_new(
        self, plan: SearchPlan, known_keys: Callable[[list[JobLead]], set[str]], max_details: int,
    ) -> SourceCollection:
        async with AsyncExitStack() as stack:
            session = await open_mcp_session(stack, self.server)
            ids = await self._search(session, plan)
            leads = [JobLead(source=self.name, external_id=job_id,
                             url=f"https://www.linkedin.com/jobs/view/{job_id}/") for job_id in ids]
            known = known_keys(leads) if leads else set()
            admitted = [lead for lead in leads if lead_key(lead) not in known][:max_details]
            log.info("[linkedin] %d unique ids, %d known, %d to fetch", len(leads), len(known), len(admitted))
            jobs, errors = await self._fetch_details(session, admitted)
            return SourceCollection(jobs=jobs, leads=len(leads), known=len(known), detail_errors=errors)

    async def _collect(self, plan: SearchPlan, admit: Callable[[JobLead], bool], max_details: int) -> list[JobPosting]:
        async with AsyncExitStack() as stack:
            session = await open_mcp_session(stack, self.server)
            ids = await self._search(session, plan)

            admitted: list[JobLead] = []
            for job_id in ids:
                if len(admitted) >= max_details:
                    break
                lead = JobLead(source=self.name, external_id=job_id, url=f"https://www.linkedin.com/jobs/view/{job_id}/")
                if admit(lead):
                    admitted.append(lead)
            log.info("[linkedin] %d unique ids, %d new (details budget %d)", len(ids), len(admitted), max_details)

            postings, _ = await self._fetch_details(session, admitted)
            return postings

    async def _fetch_details(self, session: ClientSession, admitted: list[JobLead]) -> tuple[list[JobPosting], list[str]]:
        postings: list[JobPosting] = []
        errors: list[str] = []
        started = time.monotonic()
        for n, lead in enumerate(admitted, 1):
            t0 = time.monotonic()
            try:
                data = await call_tool(session, "get_job_details", {"job_id": lead.external_id}, self.tool_timeout)
            except McpToolError as exc:
                errors.append(f"{lead.external_id}: {exc}")
                log.warning("[linkedin] detail %d/%d (%s) failed: %s", n, len(admitted), lead.external_id, exc)
                continue
            text = (data.get("sections") or {}).get("job_posting", "")
            if not text:
                errors.append(f"{lead.external_id}: no job posting text")
                log.warning("[linkedin] detail %d/%d (%s) returned no text: %s",
                            n, len(admitted), lead.external_id, data.get("section_errors"))
                continue
            posting = parse_job_posting(lead.external_id, data.get("url") or lead.url, text)
            postings.append(posting)
            log.info("[linkedin] detail %d/%d in %.0fs: %s @ %s", n, len(admitted), time.monotonic() - t0,
                     posting.title or "?", posting.company or "?")
        log.info("[linkedin] %d details fetched in %.0fs", len(postings), time.monotonic() - started)
        return postings, errors

    async def _search(self, session: ClientSession, plan: SearchPlan) -> list[str]:
        ids: dict[str, None] = {}  # ordered set: newest first within each query
        for query in plan.queries:
            args: dict = {
                "keywords": query.keywords,
                "max_pages": self.max_pages,
                "sort_by": "date",
                "date_posted": linkedin_date_filter(plan.posted_within_days),
            }
            if query.location:
                args["location"] = query.location
            if plan.work_types:
                args["work_type"] = ",".join(plan.work_types)
            if plan.experience_levels:
                args["experience_level"] = ",".join(plan.experience_levels)
            try:
                data = await call_tool(session, "search_jobs", args, self.tool_timeout)
            except McpToolError as exc:  # one failing query must not stop the others
                log.warning("[linkedin] search %r @ %r failed: %s", query.keywords, query.location, exc)
                continue
            found = [str(i) for i in data.get("job_ids") or []]
            if errors := data.get("section_errors"):
                log.info("[linkedin] %r @ %r: %s", query.keywords, query.location, errors)
            log.info("[linkedin] %r @ %r -> %d ids", query.keywords, query.location, len(found))
            ids.update(dict.fromkeys(found))
        return list(ids)
