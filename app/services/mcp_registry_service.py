"""Validation and tenant-safe lifecycle management for external MCP servers."""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app.audit import write_audit
from app.config import settings
from app.domain.mcp_registry import McpServerRegistration

_ENV_REFERENCE = re.compile(r"^(?:env://)?[A-Z][A-Z0-9_]{1,127}$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_OAUTH_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class McpRegistryService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _normalize_endpoint(value: object) -> str:
        raw = str(value).strip()
        try:
            parsed = urlsplit(raw)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("E_MCP_ENDPOINT:Endpoint 必须是有效的 HTTP(S) URL") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in raw)
        ):
            raise ValueError("E_MCP_ENDPOINT:Endpoint 禁止凭据、查询参数、片段和非法空白")
        is_loopback = hostname.casefold() == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                is_loopback = False
        if parsed.scheme == "http" and not is_loopback:
            raise ValueError("E_MCP_ENDPOINT_TLS:远程 MCP Endpoint 必须使用 HTTPS")
        normalized_path = parsed.path or "/"
        return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))

    @staticmethod
    def _normalize_tools(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not value or len(value) > 100:
            raise ValueError("E_MCP_TOOL_ALLOWLIST:必须配置 1 到 100 个精确工具名")
        result = []
        seen = set()
        for item in value:
            tool = str(item).strip()
            if not _TOOL_NAME.fullmatch(tool) or "*" in tool:
                raise ValueError("E_MCP_TOOL_ALLOWLIST:工具白名单只允许精确工具名，禁止通配符")
            if tool not in seen:
                seen.add(tool)
                result.append(tool)
        return tuple(result)

    @staticmethod
    def _normalize_oauth_scopes(value: object) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if not isinstance(value, (list, tuple)) or len(value) > 20:
            raise ValueError("E_MCP_OAUTH_SCOPE:OAuth scope 必须是最多 20 项的列表")
        result = []
        seen = set()
        for item in value:
            scope = str(item).strip()
            if not _OAUTH_SCOPE.fullmatch(scope):
                raise ValueError("E_MCP_OAUTH_SCOPE:OAuth scope 格式无效")
            if scope not in seen:
                seen.add(scope)
                result.append(scope)
        return tuple(result)

    @classmethod
    def _normalize(cls, data: dict) -> dict:
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 120:
            raise ValueError("E_MCP_NAME:Server 名称长度必须为 1 到 120")
        if data.get("transport") != "streamable-http":
            raise ValueError("E_MCP_TRANSPORT:当前只允许 streamable-http")
        auth_env_ref = str(data.get("auth_env_ref", "")).strip()
        if not _ENV_REFERENCE.fullmatch(auth_env_ref):
            raise ValueError("E_MCP_SECRET_REF:凭据引用必须是 env://大写变量名或兼容的大写变量名")
        auth_type = str(data.get("auth_type") or "bearer").strip()
        if auth_type not in {"bearer", "oauth_client_credentials"}:
            raise ValueError("E_MCP_AUTH_TYPE:认证类型不受支持")
        oauth_token_url = str(data.get("oauth_token_url") or "").strip()
        oauth_client_id = str(data.get("oauth_client_id") or "").strip()
        oauth_scopes = cls._normalize_oauth_scopes(data.get("oauth_scopes"))
        if auth_type == "oauth_client_credentials":
            if not oauth_token_url or not oauth_client_id or len(oauth_client_id) > 200 or any(character.isspace() for character in oauth_client_id):
                raise ValueError("E_MCP_OAUTH_CONFIG:OAuth Token URL 和 Client ID 不能为空")
            try:
                oauth_token_url = cls._normalize_endpoint(oauth_token_url)
            except ValueError as exc:
                raise ValueError("E_MCP_OAUTH_ENDPOINT:OAuth Token URL 必须是安全 HTTP(S) 地址") from exc
        elif oauth_token_url or oauth_client_id or oauth_scopes:
            raise ValueError("E_MCP_OAUTH_CONFIG:Bearer 模式不能携带 OAuth 配置")
        if data.get("read_only_only") is not True:
            raise ValueError("E_MCP_READ_ONLY:当前阶段只允许只读 MCP Server")
        try:
            timeout = float(data.get("request_timeout_seconds", 10.0))
            max_concurrency = int(data.get("max_concurrency", 4))
            daily_call_limit = int(data.get("daily_call_limit", 1000))
            failure_threshold = int(data.get("circuit_failure_threshold", 3))
            reset_seconds = int(data.get("circuit_reset_seconds", 30))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("E_MCP_LIMITS:MCP 调用治理参数格式无效") from exc
        if not math.isfinite(timeout) or not 1 <= timeout <= 120:
            raise ValueError("E_MCP_LIMITS:请求超时必须在 1 到 120 秒之间")
        if not 1 <= max_concurrency <= 32:
            raise ValueError("E_MCP_LIMITS:并发上限必须在 1 到 32 之间")
        if not 1 <= daily_call_limit <= 1_000_000:
            raise ValueError("E_MCP_LIMITS:每日调用额度必须在 1 到 1000000 之间")
        if not 1 <= failure_threshold <= 20:
            raise ValueError("E_MCP_LIMITS:熔断失败阈值必须在 1 到 20 之间")
        if not 1 <= reset_seconds <= 3600:
            raise ValueError("E_MCP_LIMITS:熔断恢复时间必须在 1 到 3600 秒之间")
        return {
            **data,
            "name": name,
            "transport": "streamable-http",
            "endpoint": cls._normalize_endpoint(data.get("endpoint", "")),
            "auth_type": auth_type,
            "auth_env_ref": auth_env_ref,
            "oauth_token_url": oauth_token_url,
            "oauth_client_id": oauth_client_id,
            "oauth_scopes": oauth_scopes,
            "allowed_tools": cls._normalize_tools(data.get("allowed_tools")),
            "enabled": bool(data.get("enabled", True)),
            "read_only_only": True,
            "allow_private_network": bool(data.get("allow_private_network", False)),
            "request_timeout_seconds": timeout,
            "max_concurrency": max_concurrency,
            "daily_call_limit": daily_call_limit,
            "circuit_failure_threshold": failure_threshold,
            "circuit_reset_seconds": reset_seconds,
        }

    @staticmethod
    def _audit(event: str, *, server_id: str, tenant_id: str, actor_id: str) -> None:
        write_audit(
            settings.audit_log_path,
            trace_id=str(uuid4()),
            task_id=server_id,
            tenant_id=tenant_id,
            sql="",
            duration_ms=0,
            event=event,
            actor_id=actor_id,
        )

    def list_servers(self, tenant_id: str) -> list[McpServerRegistration]:
        return self.repository.list(tenant_id)

    def get_server(self, server_id: str, tenant_id: str) -> McpServerRegistration | None:
        return self.repository.get(server_id, tenant_id)

    def create_server(self, *, tenant_id: str, actor_id: str, **data) -> McpServerRegistration:
        normalized = self._normalize(data)
        if any(
            server.name.casefold() == normalized["name"].casefold()
            for server in self.repository.list(tenant_id)
        ):
            raise ValueError("E_MCP_DUPLICATE:当前租户已存在同名 MCP Server")
        server = McpServerRegistration(tenant_id=tenant_id, **normalized)
        created = self.repository.create(server)
        self._audit("mcp_server_created", server_id=created.id, tenant_id=tenant_id, actor_id=actor_id)
        return created

    def update_server(
        self,
        server_id: str,
        tenant_id: str,
        changes: dict,
        *,
        actor_id: str,
    ) -> McpServerRegistration:
        current = self.repository.get(server_id, tenant_id)
        if not current:
            raise LookupError("MCP Server 不存在")
        data = {
            "name": current.name,
            "transport": current.transport,
            "endpoint": current.endpoint,
            "auth_env_ref": current.auth_env_ref,
            "auth_type": current.auth_type,
            "oauth_token_url": current.oauth_token_url,
            "oauth_client_id": current.oauth_client_id,
            "oauth_scopes": current.oauth_scopes,
            "allowed_tools": current.allowed_tools,
            "enabled": current.enabled,
            "read_only_only": current.read_only_only,
            "allow_private_network": current.allow_private_network,
            "request_timeout_seconds": current.request_timeout_seconds,
            "max_concurrency": current.max_concurrency,
            "daily_call_limit": current.daily_call_limit,
            "circuit_failure_threshold": current.circuit_failure_threshold,
            "circuit_reset_seconds": current.circuit_reset_seconds,
            **changes,
        }
        normalized = self._normalize(data)
        if any(
            server.id != server_id
            and server.name.casefold() == normalized["name"].casefold()
            for server in self.repository.list(tenant_id)
        ):
            raise ValueError("E_MCP_DUPLICATE:当前租户已存在同名 MCP Server")
        credential_changed = any(
            normalized[field] != getattr(current, field)
            for field in ("auth_type", "auth_env_ref", "oauth_token_url", "oauth_client_id", "oauth_scopes")
        )
        if credential_changed:
            raise ValueError("E_MCP_CREDENTIAL_ROTATION_REQUIRED:认证配置必须通过凭据轮换接口修改")
        governance_changed = any(
            normalized[field] != getattr(current, field)
            for field in (
                "endpoint",
                "allowed_tools",
                "enabled",
                "read_only_only",
                "allow_private_network",
                "request_timeout_seconds",
            )
        )
        updated = replace(
            current,
            **normalized,
            health_status="unchecked" if governance_changed else current.health_status,
            last_latency_ms=0 if governance_changed else current.last_latency_ms,
            last_error_code="" if governance_changed else current.last_error_code,
            discovered_tools=() if governance_changed else current.discovered_tools,
            last_checked_at=None if governance_changed else current.last_checked_at,
            updated_at=datetime.now(timezone.utc),
        )
        saved = self.repository.update(updated)
        self._audit("mcp_server_updated", server_id=saved.id, tenant_id=tenant_id, actor_id=actor_id)
        return saved

    def rotate_credentials(
        self,
        server_id: str,
        tenant_id: str,
        changes: dict,
        *,
        actor_id: str,
    ) -> McpServerRegistration:
        current = self.repository.get(server_id, tenant_id)
        if not current:
            raise LookupError("MCP Server 不存在")
        allowed = {"auth_type", "auth_env_ref", "oauth_token_url", "oauth_client_id", "oauth_scopes"}
        if not changes or set(changes) - allowed:
            raise ValueError("E_MCP_CREDENTIAL_ROTATION:凭据轮换字段无效")
        data = {
            "name": current.name,
            "transport": current.transport,
            "endpoint": current.endpoint,
            "auth_type": current.auth_type,
            "auth_env_ref": current.auth_env_ref,
            "oauth_token_url": current.oauth_token_url,
            "oauth_client_id": current.oauth_client_id,
            "oauth_scopes": current.oauth_scopes,
            "allowed_tools": current.allowed_tools,
            "enabled": current.enabled,
            "read_only_only": current.read_only_only,
            "allow_private_network": current.allow_private_network,
            "request_timeout_seconds": current.request_timeout_seconds,
            "max_concurrency": current.max_concurrency,
            "daily_call_limit": current.daily_call_limit,
            "circuit_failure_threshold": current.circuit_failure_threshold,
            "circuit_reset_seconds": current.circuit_reset_seconds,
            **changes,
        }
        normalized = self._normalize(data)
        timestamp = datetime.now(timezone.utc)
        rotated = replace(
            current,
            **normalized,
            credential_version=current.credential_version + 1,
            credential_rotated_at=timestamp,
            health_status="unchecked",
            last_latency_ms=0,
            last_error_code="",
            discovered_tools=(),
            last_checked_at=None,
            updated_at=timestamp,
        )
        saved = self.repository.update(rotated)
        self._audit("mcp_credentials_rotated", server_id=saved.id, tenant_id=tenant_id, actor_id=actor_id)
        return saved

    def delete_server(self, server_id: str, tenant_id: str, *, actor_id: str) -> None:
        if not self.repository.delete(server_id, tenant_id):
            raise LookupError("MCP Server 不存在")
        self._audit("mcp_server_deleted", server_id=server_id, tenant_id=tenant_id, actor_id=actor_id)

    def record_runtime_status(
        self,
        server_id: str,
        tenant_id: str,
        *,
        status: str,
        latency_ms: int = 0,
        error_code: str = "",
        discovered_tools: tuple[str, ...] = (),
        checked_at: datetime | None = None,
    ) -> McpServerRegistration:
        current = self.repository.get(server_id, tenant_id)
        if not current:
            raise LookupError("MCP Server 不存在")
        timestamp = checked_at or datetime.now(timezone.utc)
        updated = replace(
            current,
            health_status=status,
            last_latency_ms=max(0, int(latency_ms)),
            last_error_code=str(error_code)[:64],
            discovered_tools=tuple(discovered_tools),
            last_checked_at=timestamp,
            updated_at=timestamp,
        )
        return self.repository.update(updated)

