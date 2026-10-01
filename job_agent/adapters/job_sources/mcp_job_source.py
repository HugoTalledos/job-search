"""JobSource port backed by an MCP server (LinkedIn or any other job-board MCP server).

The server is launched over stdio, its tools are handed to the Claude search agent, and the agent
returns validated postings. Nothing in the core knows the server's tool names.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ...domain.models import JobPosting, SearchCriteria
from .claude_search_agent import run_search_agent

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


class McpJobSource:
    def __init__(self, server: McpServerParams) -> None:
        self.server = server

    @property
    def name(self) -> str:
        return self.server.name

    def search(self, criteria: SearchCriteria) -> list[JobPosting]:
        return asyncio.run(self._search(criteria))

    async def _search(self, criteria: SearchCriteria) -> list[JobPosting]:
        async with AsyncExitStack() as stack:
            session = await open_mcp_session(stack, self.server)
            listed = await session.list_tools()
            log.info("[%s] %d MCP tools: %s", self.name, len(listed.tools), [t.name for t in listed.tools])
            tools = [async_mcp_tool(t, session) for t in listed.tools if t.name != "submit_jobs"]
            return await run_search_agent(self.name, tools, criteria)
