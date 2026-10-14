"""MCP tool failures that FastMCP reports as isError results."""

from __future__ import annotations


class MCPToolError(Exception):
    """Structured tool failure for Agent clients."""

    def __init__(self, code: str, message: str, recovery: dict[str, object] | None = None) -> None:
        import json

        self.code = code
        self.message = message
        self.recovery = recovery
        suffix = "; recovery=" + json.dumps(recovery, ensure_ascii=False) if recovery else ""
        super().__init__(f"{code}: {message}{suffix}")
