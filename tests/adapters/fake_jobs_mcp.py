"""Minimal stdio MCP server used in tests."""
from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-jobs")


@server.tool()
def search_jobs(keywords: str, location: str = "") -> str:
    """Search job postings."""
    return f"1 result for {keywords} in {location}: Backend Engineer at Acme (id 123)"


if __name__ == "__main__":
    server.run()
