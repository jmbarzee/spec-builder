"""A one-tool stdio MCP server for the step-agent tests."""

from mcp.server.mcpserver import MCPServer

server = MCPServer("notes")


@server.tool()
def write_note(text: str) -> str:
    """Record a note."""
    return f"noted: {text}"


if __name__ == "__main__":
    server.run("stdio")
