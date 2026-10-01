"""JobSource port backed by Claude's server-side web search, restricted to job-board domains."""

from __future__ import annotations

import asyncio

from ...domain.models import JobPosting, SearchCriteria
from .claude_search_agent import run_search_agent


class WebSearchJobSource:
    name = "web_search"

    def __init__(self, allowed_domains: list[str] | None = None, max_uses: int = 15) -> None:
        self.tool: dict = {"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}
        if allowed_domains:
            self.tool["allowed_domains"] = allowed_domains

    def search(self, criteria: SearchCriteria) -> list[JobPosting]:
        return asyncio.run(run_search_agent("web", [self.tool], criteria))
