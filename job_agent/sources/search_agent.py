"""Job search agent.

Claude drives the tools of every configured MCP server (LinkedIn first, any other job-board MCP
server can be added in config.yaml) plus, optionally, the server-side web search tool. Results are
reported back through the ``submit_jobs`` tool so they arrive validated as ``JobPosting`` objects.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack

from anthropic import beta_async_tool
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .. import llm
from ..config import SearchConfig
from ..models import JobPosting, Profile

log = logging.getLogger(__name__)

MAX_PAUSE_RESTARTS = 5

SYSTEM = """You are a job-search agent working for one candidate.
Use the available job-board tools to find CURRENT postings that fit the candidate profile.

How to work:
- Run several searches: combine the candidate's target roles / keywords with each preferred location.
- Prefer postings published within the requested time window; skip companies on the exclude list.
- Skip postings whose urls are in `already_seen_urls`.
- For promising postings, fetch the full details so `description` contains the real requirements.
- Do not judge fit too strictly - a separate step scores each posting. Discard only clear mismatches
  (wrong profession, wrong seniority by far, wrong country with no remote option).
- When you have collected up to the requested maximum, call `submit_jobs` once with all of them,
  then stop. If nothing was found, call `submit_jobs` with an empty list.
- Copy titles, companies, urls and descriptions exactly from the tool results; never fabricate postings."""


async def _connect(stack: AsyncExitStack, spec) -> ClientSession:
    params = StdioServerParameters(command=spec.command, args=spec.args, env={**_base_env(), **spec.env})
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session


def _base_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "USER", "LANG", "TMPDIR", "XDG_CACHE_HOME", "UV_CACHE_DIR",
            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
    return {k: v for k, v in os.environ.items() if k in keep}


async def search_jobs(profile: Profile, cfg: SearchConfig, seen_urls: list[str]) -> list[JobPosting]:
    collected: list[JobPosting] = []

    @beta_async_tool
    async def submit_jobs(jobs: list[JobPosting]) -> str:
        """Submit the job postings found. Call once at the end with every posting worth scoring.

        Args:
            jobs: Postings found, with the full description text copied from the source.
        """
        collected.extend(jobs)
        return f"Received {len(jobs)} postings. You are done; do not call any more tools."

    async with AsyncExitStack() as stack:
        tools: list = [submit_jobs]
        names = {"submit_jobs"}
        for spec in cfg.mcp_servers:
            if not spec.enabled:
                continue
            try:
                session = await _connect(stack, spec)
                listed = await session.list_tools()
            except Exception as exc:  # one broken source must not stop the others
                log.warning("MCP server %s unavailable: %s", spec.name, exc)
                continue
            for tool in listed.tools:
                if tool.name in names:
                    log.warning("Skipping duplicate tool name %s from %s", tool.name, spec.name)
                    continue
                names.add(tool.name)
                tools.append(async_mcp_tool(tool, session))
            log.info("MCP server %s: %d tools", spec.name, len(listed.tools))

        if cfg.web_search:
            web: dict = {"type": "web_search_20260209", "name": "web_search", "max_uses": 15}
            if cfg.web_search_domains:
                web["allowed_domains"] = cfg.web_search_domains
            tools.append(web)

        if len(tools) == 1:
            log.error("No job sources available (no MCP server connected and web_search disabled)")
            return []

        brief = {
            "target_roles": profile.target_roles,
            "keywords": profile.search_keywords + cfg.extra_keywords,
            "seniority": profile.seniority,
            "top_skills": [s.name for s in profile.skills[:15]],
            "locations": cfg.locations or profile.locations,
            "posted_within_days": cfg.posted_within_days,
            "max_postings": cfg.max_jobs_per_run,
            "exclude_companies": cfg.exclude_companies,
            "already_seen_urls": seen_urls[-300:],
        }
        messages: list = [{"role": "user", "content": "Search brief:\n" + json.dumps(brief, ensure_ascii=False, indent=2)}]

        for _ in range(MAX_PAUSE_RESTARTS + 1):
            runner = llm.async_client().beta.messages.tool_runner(
                model=llm.MODEL,
                max_tokens=16000,
                system=SYSTEM,
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                betas=[llm.FALLBACK_BETA],
                fallbacks="default",
                max_iterations=40,
            )
            last = None
            async for message in runner:
                last = message
                messages.append({"role": "assistant", "content": message.content})
                if (tool_response := await runner.generate_tool_call_response()) is not None:
                    messages.append(tool_response)
            # The runner does not resume server-tool pauses on its own; restart with the paused turn.
            if last is None or last.stop_reason != "pause_turn" or collected:
                if last is not None and last.stop_reason == "refusal":
                    log.warning("Search agent refused the request")
                break

    log.info("Search agent submitted %d postings", len(collected))
    return collected[: cfg.max_jobs_per_run]
