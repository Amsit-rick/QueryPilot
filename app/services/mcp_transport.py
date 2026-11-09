"""Official MCP 2.x Streamable HTTP client adapter."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from app.config import settings
from app.domain.mcp_invocation import McpToolDescriptor


class OfficialMcpTransport:
    @asynccontextmanager
    async def _session(self, server, token: str):
        timeout = httpx2.Timeout(server.request_timeout_seconds)
        limits = httpx2.Limits(
            max_connections=max(1, server.max_concurrency),
            max_keepalive_connections=max(1, server.max_concurrency),
        )
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
            trust_env=False,
        ) as http_client:
            async with streamable_http_client(
                server.endpoint,
                http_client=http_client,
                terminate_on_close=True,
                max_sse_event_size=settings.mcp_max_sse_event_bytes,
            ) as streams:
                async with ClientSession(
                    *streams,
                    read_timeout_seconds=server.request_timeout_seconds,
                ) as session:
                    await session.initialize()
                    yield session

    @staticmethod
    def _descriptor(tool) -> McpToolDescriptor:
        annotations = tool.annotations
        return McpToolDescriptor(
            name=tool.name,
            read_only=getattr(annotations, "read_only_hint", None),
            destructive=getattr(annotations, "destructive_hint", None),
            idempotent=getattr(annotations, "idempotent_hint", None),
        )

    async def _list_tools(self, session: ClientSession) -> list[McpToolDescriptor]:
        tools = []
        cursor = None
        for _page in range(settings.mcp_max_tool_pages):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await session.list_tools(params=params)
            tools.extend(self._descriptor(tool) for tool in result.tools)
            if len(tools) > settings.mcp_max_discovered_tools:
                raise RuntimeError("external MCP tool catalog exceeds configured limit")
            cursor = result.next_cursor
            if not cursor:
                return tools
        raise RuntimeError("external MCP tool catalog pagination exceeds configured limit")

    async def discover(self, server, token: str) -> list[McpToolDescriptor]:
        async with self._session(server, token) as session:
            return await self._list_tools(session)

    async def execute(self, server, token: str, tool_name: str, arguments: dict, validate_tools):
        async with self._session(server, token) as session:
            tools = await self._list_tools(session)
            validate_tools(tools)
            result = await session.call_tool(
                tool_name,
                arguments=arguments,
                read_timeout_seconds=server.request_timeout_seconds,
                allow_input_required=False,
            )
            if not hasattr(result, "model_dump"):
                raise RuntimeError("external MCP returned an unsupported result contract")
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)


__all__ = ["OfficialMcpTransport"]
