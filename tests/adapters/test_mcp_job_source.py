import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from anthropic.lib.tools.mcp import async_mcp_tool

from job_agent.adapters.job_sources.mcp_job_source import McpServerParams, open_mcp_session

FAKE = McpServerParams(name="fake", command=sys.executable, args=[str(Path(__file__).parent / "fake_jobs_mcp.py")])


def test_mcp_tools_are_exposed_to_claude():
    async def go():
        async with AsyncExitStack() as stack:
            session = await open_mcp_session(stack, FAKE)
            tool = async_mcp_tool((await session.list_tools()).tools[0], session)
            return tool.to_dict(), await tool.call({"keywords": "python", "location": "Remote"})

    schema, result = asyncio.run(go())
    assert schema["name"] == "search_jobs" and "keywords" in schema["input_schema"]["properties"]
    assert "Backend Engineer at Acme" in str(result)
