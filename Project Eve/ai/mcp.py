"""
MCP (Model Context Protocol) support.

Provides MCPToolBridge for connecting to remote MCP servers and
bridging their tools into OpenAI-compatible function tool schemas.
Also exports shared tool utility helpers used by agents.
"""

import json
import contextlib
from contextlib import AsyncExitStack
from typing import Any

try:
    from mcp import ClientSession
    import mcp.types as mcp_types
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:
    ClientSession = None
    mcp_types = None
    sse_client = None
    streamablehttp_client = None


def tool_schema_to_openai_tool(
    name: str,
    description: str | None,
    input_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert MCP tool metadata to an OpenAI function tool schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or "",
            "parameters": input_schema or {"type": "object", "properties": {}},
        },
    }


def normalize_tool_result(result: Any) -> str:
    """Normalize a tool call result to a plain string."""
    if result is None:
        return ""
    if hasattr(result, "content"):
        parts = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


class MCPToolBridge:
    """Bridge remote MCP tools into OpenAI-compatible function tools."""

    def __init__(self, servers: list[dict[str, str]] | None = None):
        self.servers = servers or []
        self.exit_stack: AsyncExitStack | None = None
        self.sessions: dict[str, Any] = {}
        self.tool_to_session: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def connect(self) -> None:
        if not self.servers:
            return
        if ClientSession is None:
            raise RuntimeError(
                "MCP support requires the `mcp` package. Install with: pip install mcp"
            )

        self.exit_stack = AsyncExitStack()
        for index, server in enumerate(self.servers):
            name = server.get("name") or f"mcp_{index}"
            url = server.get("url")
            transport = server.get("transport", server.get("type", "streamable_http"))
            headers = dict(server.get("headers") or {})
            protocol_version = server.get("protocol_version")
            if not url:
                raise ValueError(f"MCP server {name} is missing url")

            if protocol_version:
                headers["mcp-protocol-version"] = protocol_version

            old_protocol_version = None
            if protocol_version and mcp_types is not None:
                old_protocol_version = mcp_types.LATEST_PROTOCOL_VERSION
                mcp_types.LATEST_PROTOCOL_VERSION = protocol_version

            try:
                if transport == "sse":
                    read_stream, write_stream = await self.exit_stack.enter_async_context(
                        sse_client(url, headers=headers)
                    )
                elif transport in {"streamable_http", "streamablehttp", "http"}:
                    read_stream, write_stream, _ = await self.exit_stack.enter_async_context(
                        streamablehttp_client(url, headers=headers)
                    )
                else:
                    raise ValueError(f"Unsupported MCP transport: {transport}")

                session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                self.sessions[name] = session
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await self.close()
                raise RuntimeError(
                    f"Failed to initialize MCP server {name} at {url}. "
                    f"Check token, transport, URL, and protocol_version. Original error: {exc}"
                ) from exc
            finally:
                if old_protocol_version is not None and mcp_types is not None:
                    mcp_types.LATEST_PROTOCOL_VERSION = old_protocol_version

            tool_result = await session.list_tools()
            for tool in tool_result.tools:
                tool_name = f"{name}__{tool.name}"
                self.tool_to_session[tool_name] = (session, tool.name)
                self.tools.append(
                    tool_schema_to_openai_tool(
                        name=tool_name,
                        description=getattr(tool, "description", None),
                        input_schema=getattr(tool, "inputSchema", None),
                    )
                )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name not in self.tool_to_session:
            raise ValueError(f"Unknown MCP tool: {tool_name}")
        session, original_name = self.tool_to_session[tool_name]
        result = await session.call_tool(original_name, arguments)
        return normalize_tool_result(result)

    async def close(self) -> None:
        if self.exit_stack:
            await self.exit_stack.aclose()
            self.exit_stack = None
