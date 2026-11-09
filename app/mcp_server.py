"""Standard MCP 2.x adapter for Query Pilot's governed read-only capabilities."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp_types import ToolAnnotations

from app.auth import require_identity
from app.rbac import authorize
from app.services.read_only_integration_service import (
    ReadOnlyIntegrationError,
    get_read_only_integration_service,
)
from app.services.skill_service import get_skill_service


IdentityProvider = Callable[[Context | None], dict[str, Any]]
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def resolve_mcp_identity(context: Context | None = None) -> dict[str, Any]:
    """Map HTTP Bearer or stdio environment credentials to Query Pilot identity."""
    headers = context.headers if context is not None else None
    authorization = headers.get("authorization") if headers else None
    # HTTP requests must authenticate independently. Never let a process-level
    # stdio token silently become a shared identity for unauthenticated callers.
    if not authorization and headers is None:
        token = os.getenv("QUERY_PILOT_MCP_TOKEN", "").strip()
        if token:
            authorization = token if token.startswith("Bearer ") else f"Bearer {token}"
    try:
        identity = require_identity(authorization)
        authorize(identity, "query")
        return identity
    except HTTPException as exc:
        raise PermissionError("缺少或无效的 MCP 身份凭证") from exc


def _invoke_tool(identity: dict[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return get_read_only_integration_service().invoke(identity, name, arguments)
    except ReadOnlyIntegrationError as exc:
        raise ValueError(f"{exc.code}:{exc}") from exc


def create_mcp_server(identity_provider: IdentityProvider = resolve_mcp_identity) -> MCPServer:
    server = MCPServer(
        name="query-pilot",
        title="Query Pilot Read-only Data Agent",
        description="Governed Schema, query planning, task result, and skill routing tools.",
        instructions=(
            "All tools are read-only. Never claim that query.plan executed SQL. "
            "Use the Query Pilot application query endpoint for governed execution."
        ),
        version="1.0.0",
    )

    @server.tool(
        name="schema.list",
        description="列出当前身份在指定数据源中有权查看的表、字段和关系。",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def schema_list(datasource_id: str | None = None, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
        return _invoke_tool(identity_provider(ctx), "schema.list", {"datasource_id": datasource_id})

    @server.tool(
        name="query.plan",
        description="校验候选只读 SQL 和查询计划；不会执行 SQL。",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def query_plan(
        question: str,
        datasource_id: str | None = None,
        sql: str | None = None,
        require_approval: bool = False,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return _invoke_tool(
            identity_provider(ctx),
            "query.plan",
            {
                "question": question,
                "datasource_id": datasource_id,
                "sql": sql,
                "require_approval": require_approval,
            },
        )

    @server.tool(
        name="task.status",
        description="读取当前身份拥有的数据任务状态和最近事件。",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def task_status(task_id: str, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
        return _invoke_tool(identity_provider(ctx), "task.status", {"task_id": task_id})

    @server.tool(
        name="result.get",
        description="读取当前身份拥有任务的持久化结果资产；不会重新执行 SQL。",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def result_get(task_id: str, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
        return _invoke_tool(identity_provider(ctx), "result.get", {"task_id": task_id})

    @server.tool(
        name="skill.route",
        description="确定性选择适合问题的只读分析 Skill，并返回匹配证据。",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    def skill_route(
        question: str,
        datasource_id: str | None = None,
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        identity = identity_provider(ctx)
        if datasource_id:
            _invoke_tool(identity, "schema.list", {"datasource_id": datasource_id})
        return get_skill_service().route(
            question,
            identity=identity,
            datasource_id=datasource_id,
        )

    @server.resource(
        "querypilot://skills/catalog/{tenant_id}",
        name="Query Pilot Skill Catalog",
        description="当前身份可使用的版本化只读分析 Skill。",
        mime_type="application/json",
    )
    def skill_catalog(tenant_id: str, ctx: Context) -> str:
        identity = identity_provider(ctx)
        if tenant_id != str(identity.get("tenant_id", "")):
            raise PermissionError("无权读取其他租户的 Skill 目录")
        payload = {
            "version": 1,
            "read_only": True,
            "skills": get_skill_service().list_skills(identity),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    return server


__all__ = ["create_mcp_server", "resolve_mcp_identity"]
