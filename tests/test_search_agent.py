import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from anthropic.lib.tools.mcp import async_mcp_tool

from job_agent.config import McpServerSpec
from job_agent.sources.search_agent import _connect


def test_mcp_tools_are_exposed_to_claude():
    spec = McpServerSpec(name="fake", command=sys.executable, args=[str(Path(__file__).parent / "fake_jobs_mcp.py")])

    async def go():
        async with AsyncExitStack() as stack:
            session = await _connect(stack, spec)
            tools = (await session.list_tools()).tools
            tool = async_mcp_tool(tools[0], session)
            schema = tool.to_dict()
            result = await tool.call({"keywords": "python", "location": "Remote"})
            return schema, result

    schema, result = asyncio.run(go())
    assert schema["name"] == "search_jobs" and "keywords" in schema["input_schema"]["properties"]
    assert "Backend Engineer at Acme" in str(result)
