"""MCP tool failures that FastMCP reports as isError results."""

from __future__ import annotations


class MCPToolError(Exception):
    """Structured tool failure for Agent clients."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
