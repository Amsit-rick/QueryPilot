"""Administrator API for governed external MCP server registrations."""

import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.dependencies import require_manage_access
from app.mcp_registry_runtime import get_mcp_registry_service
from app.mcp_invocation_runtime import get_mcp_invocation_service
from app.schemas.mcp_registry import McpServerCreateRequest, McpServerUpdateRequest, McpToolCallRequest
from app.services.mcp_invocation_service import McpInvocationError

router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-registry"])


def _payload(server) -> dict:
    data = asdict(server)
    data["allowed_tools"] = list(server.allowed_tools)
    data["discovered_tools"] = list(server.discovered_tools)
    data["created_at"] = server.created_at.isoformat()
    data["updated_at"] = server.updated_at.isoformat()
    data["last_checked_at"] = server.last_checked_at.isoformat() if server.last_checked_at else None
    data["credential_configured"] = bool(os.getenv(server.auth_env_ref, ""))
    return data


def _invocation_http_error(exc: McpInvocationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.as_dict())


@router.get("")
def list_servers(identity=Depends(require_manage_access)):
    tenant_id = identity.get("tenant_id", "default")
    return [_payload(server) for server in get_mcp_registry_service().list_servers(tenant_id)]


@router.post("")
def create_server(request: McpServerCreateRequest, identity=Depends(require_manage_access)):
    try:
        server = get_mcp_registry_service().create_server(
            tenant_id=identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
            **request.model_dump(),
        )
        return _payload(server)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{server_id}")
def update_server(
    server_id: str,
    request: McpServerUpdateRequest,
    identity=Depends(require_manage_access),
):
    try:
        server = get_mcp_registry_service().update_server(
            server_id,
            identity.get("tenant_id", "default"),
            request.model_dump(exclude_unset=True),
            actor_id=str(identity.get("subject", "")),
        )
        return _payload(server)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{server_id}/health")
async def check_server_health(server_id: str, identity=Depends(require_manage_access)):
    try:
        server = await get_mcp_invocation_service().check_health(
            server_id,
            identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
        )
        return _payload(server)
    except McpInvocationError as exc:
        raise _invocation_http_error(exc) from exc


@router.post("/{server_id}/invoke")
async def invoke_server_tool(
    server_id: str,
    request: McpToolCallRequest,
    identity=Depends(require_manage_access),
):
    try:
        return await get_mcp_invocation_service().invoke_tool(
            server_id,
            identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
            tool_name=request.tool_name,
            arguments=request.arguments,
        )
    except McpInvocationError as exc:
        raise _invocation_http_error(exc) from exc


@router.delete("/{server_id}", status_code=204)
def delete_server(server_id: str, identity=Depends(require_manage_access)):
    try:
        get_mcp_registry_service().delete_server(
            server_id,
            identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
        )
        return Response(status_code=204)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

