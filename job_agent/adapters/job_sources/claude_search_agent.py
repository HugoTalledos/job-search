"""Claude agent loop shared by the job-source adapters.

The agent receives a source's tools (MCP tools, server-side web search...) plus ``submit_jobs``, searches
according to the ``SearchCriteria`` and reports postings through ``submit_jobs`` so they arrive validated.
"""

from __future__ import annotations

import logging

from anthropic import beta_async_tool

from ...domain.models import JobPosting, SearchCriteria
from ..llm import claude_client

log = logging.getLogger(__name__)

MAX_PAUSE_RESTARTS = 5

SYSTEM = """You are a job-search agent working for one candidate.
Use the available job-board tools to find CURRENT postings that fit the search criteria.

How to work:
- Run several searches: combine the candidate's target roles / keywords with each preferred location.
- Prefer postings published within `posted_within_days`; skip companies in `exclude_companies`.
- Skip postings whose urls are in `already_seen_urls`.
- For promising postings, fetch the full details so `description` contains the real requirements.
- Do not judge fit too strictly - a separate step scores each posting. Discard only clear mismatches
  (wrong profession, wrong seniority by far, wrong country with no remote option).
- When you have collected up to `max_results`, call `submit_jobs` once with all of them, then stop.
  If nothing was found, call `submit_jobs` with an empty list.
- Copy titles, companies, urls and descriptions exactly from the tool results; never fabricate postings.
- Set `source` to: {source}."""


async def run_search_agent(source: str, tools: list, criteria: SearchCriteria) -> list[JobPosting]:
    collected: list[JobPosting] = []

    @beta_async_tool
    async def submit_jobs(jobs: list[JobPosting]) -> str:
        """Submit the job postings found. Call once at the end with every posting worth scoring.

        Args:
            jobs: Postings found, with the full description text copied from the source.
        """
        collected.extend(jobs)
        return f"Received {len(jobs)} postings. You are done; do not call any more tools."

    messages: list = [{"role": "user", "content": "Search criteria:\n" + criteria.model_dump_json(indent=2)}]
    for _ in range(MAX_PAUSE_RESTARTS + 1):
        runner = claude_client.async_client().beta.messages.tool_runner(
            model=claude_client.MODEL,
            max_tokens=16000,
            system=SYSTEM.format(source=source),
            tools=[submit_jobs, *tools],
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=[claude_client.FALLBACK_BETA],
            fallbacks="default",
            max_iterations=40,
        )
        last = None
        async for message in runner:
            last = message
            messages.append({"role": "assistant", "content": message.content})
            if (tool_response := await runner.generate_tool_call_response()) is not None:
                messages.append(tool_response)
        if last is not None and last.stop_reason == "refusal":
            log.warning("[%s] search agent refused the request", source)
        # The runner does not resume server-tool pauses on its own; restart with the paused turn.
        if last is None or last.stop_reason != "pause_turn" or collected:
            break

    return collected[: criteria.max_results]
