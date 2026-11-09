"""Governed discovery, health checks and read-only external MCP invocation."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import os
import socket
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

from app.audit import write_audit
from app.config import settings
from app.domain.mcp_invocation import McpToolDescriptor
from app.services.mcp_runtime_guard import McpGuardError
from app.tool_replay_runtime import get_tool_replay_policy_service


_BLOCKED_INFRASTRUCTURE_ADDRESSES = frozenset(
    ipaddress.ip_address(value)
    for value in (
        "100.100.100.200",  # Alibaba Cloud instance metadata
        "168.63.129.16",  # Azure virtual platform address
        "169.254.169.253",  # AWS VPC resolver endpoint
        "169.254.169.254",  # Common cloud instance metadata
        "fd00:ec2::253",  # AWS VPC resolver IPv6 endpoint
        "fd00:ec2::254",  # AWS instance metadata IPv6 endpoint
    )
)


class McpInvocationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int, retryable: bool = False):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"{code}:{message}")

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class McpInvocationService:
    def __init__(self, registry, *, transport, guard, resolver=None, environ=None, replay_policies=None):
        self.registry = registry
        self.transport = transport
        self.guard = guard
        self._resolver = resolver or self._resolve_hostname
        self._environ = environ if environ is not None else os.environ
        self.replay_policies = replay_policies or get_tool_replay_policy_service()

    @staticmethod
    def _resolve_hostname(hostname: str) -> list[str]:
        return sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
                if item[4]
            }
        )

    async def _resolved_addresses(self, hostname: str) -> list[str]:
        try:
            result = await asyncio.to_thread(self._resolver, hostname)
            if inspect.isawaitable(result):
                result = await result
            addresses = [str(value) for value in result]
        except Exception as exc:
            raise McpInvocationError(
                "E_MCP_TARGET_RESOLUTION",
                "无法解析外部 MCP Server 地址",
                status_code=502,
                retryable=True,
            ) from exc
        if not addresses:
            raise McpInvocationError(
                "E_MCP_TARGET_RESOLUTION",
                "无法解析外部 MCP Server 地址",
                status_code=502,
                retryable=True,
            )
        return addresses

    async def _validate_target(self, server) -> None:
        hostname = urlsplit(server.endpoint).hostname
        if not hostname:
            raise McpInvocationError("E_MCP_TARGET_FORBIDDEN", "外部 MCP 目标地址无效", status_code=422)
        addresses = await self._resolved_addresses(hostname)
        for raw in addresses:
            try:
                address = ipaddress.ip_address(raw)
            except ValueError as exc:
                raise McpInvocationError("E_MCP_TARGET_FORBIDDEN", "外部 MCP 目标地址无效", status_code=422) from exc
            if (
                address in _BLOCKED_INFRASTRUCTURE_ADDRESSES
                or address.is_unspecified
                or address.is_multicast
                or address.is_link_local
                or address.is_reserved
            ):
                raise McpInvocationError(
                    "E_MCP_TARGET_FORBIDDEN",
                    "外部 MCP 目标地址属于禁止网络范围",
                    status_code=422,
                )
            if address.is_loopback:
                continue
            if address.is_private and not server.allow_private_network:
                raise McpInvocationError(
                    "E_MCP_TARGET_PRIVATE",
                    "外部 MCP 目标解析到私有网络，需管理员显式允许",
                    status_code=422,
                )
            if not address.is_private and not address.is_global:
                raise McpInvocationError(
                    "E_MCP_TARGET_FORBIDDEN",
                    "外部 MCP 目标地址不属于可调用网络范围",
                    status_code=422,
                )

    def _server(self, server_id: str, tenant_id: str):
        server = self.registry.get_server(server_id, tenant_id)
        if not server:
            raise McpInvocationError("E_MCP_NOT_FOUND", "MCP Server 不存在", status_code=404)
        return server

    @staticmethod
    def _guard_key(server) -> str:
        return f"{server.tenant_id}:{server.id}"

    def _credential(self, server) -> str:
        token = str(self._environ.get(server.auth_env_ref, "")).strip()
        if not token:
            raise McpInvocationError("E_MCP_SECRET_MISSING", "MCP Server 凭据未配置", status_code=409)
        return token

    @staticmethod
    def _tool_map(tools: list[McpToolDescriptor]) -> dict[str, McpToolDescriptor]:
        result = {}
        for tool in tools:
            if tool.name in result:
                raise McpInvocationError(
                    "E_MCP_TOOL_CATALOG_INVALID",
                    "远端工具目录包含重复名称",
                    status_code=409,
                )
            result[tool.name] = tool
        return result

    @classmethod
    def _validate_tool(cls, tools: list[McpToolDescriptor], tool_name: str) -> None:
        tool = cls._tool_map(tools).get(tool_name)
        if not tool:
            raise McpInvocationError("E_MCP_TOOL_UNAVAILABLE", "白名单工具未由远端 Server 提供", status_code=409)
        if tool.read_only is not True or tool.destructive is True:
            raise McpInvocationError(
                "E_MCP_TOOL_NOT_READ_ONLY",
                "远端工具缺少明确只读声明或被标记为破坏性",
                status_code=409,
            )

    @classmethod
    def _catalog_policy_error(cls, server, tools: list[McpToolDescriptor]) -> str:
        try:
            for tool_name in server.allowed_tools:
                cls._validate_tool(tools, tool_name)
        except McpInvocationError as exc:
            return exc.code
        return ""

    @staticmethod
    def _discovered_names(tools: list[McpToolDescriptor]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(tool.name for tool in tools))

    @staticmethod
    def _audit(event: str, *, server, actor_id: str, duration_ms: int, error_code: str = "", tool_name: str | None = None, replay_policy=None) -> None:
        write_audit(
            settings.audit_log_path,
            trace_id=str(uuid4()),
            tenant_id=server.tenant_id,
            sql="",
            duration_ms=duration_ms,
            event=event,
            actor_id=actor_id,
            error_code=error_code or None,
            integration_id=server.id,
            operation_name=tool_name,
            tool_replay_class=replay_policy.replay_class.value if replay_policy else None,
            tool_replay_policy_version=replay_policy.version if replay_policy else None,
        )

    @staticmethod
    def _from_guard(exc: McpGuardError) -> McpInvocationError:
        status = 429 if exc.code in {"E_MCP_CONCURRENCY", "E_MCP_BUDGET"} else 503
        return McpInvocationError(exc.code, exc.message, status_code=status, retryable=exc.retryable)

    async def _record_failure(self, server) -> None:
        try:
            await self.guard.record_failure(
                self._guard_key(server),
                failure_threshold=server.circuit_failure_threshold,
                reset_seconds=server.circuit_reset_seconds,
            )
        except McpGuardError as exc:
            raise self._from_guard(exc) from exc

    async def _record_success(self, server) -> None:
        try:
            await self.guard.record_success(self._guard_key(server))
        except McpGuardError as exc:
            raise self._from_guard(exc) from exc

    async def check_health(self, server_id: str, tenant_id: str, *, actor_id: str):
        server = self._server(server_id, tenant_id)
        checked_at = None
        if not server.enabled:
            updated = self.registry.record_runtime_status(server.id, tenant_id, status="disabled", error_code="E_MCP_DISABLED")
            self._audit("mcp_health_checked", server=server, actor_id=actor_id, duration_ms=0, error_code="E_MCP_DISABLED")
            return updated
        try:
            token = self._credential(server)
        except McpInvocationError as exc:
            updated = self.registry.record_runtime_status(server.id, tenant_id, status="unconfigured", error_code=exc.code)
            self._audit("mcp_health_checked", server=server, actor_id=actor_id, duration_ms=0, error_code=exc.code)
            return updated
        try:
            await self._validate_target(server)
        except McpInvocationError as exc:
            self.registry.record_runtime_status(server.id, tenant_id, status="policy_blocked", error_code=exc.code)
            self._audit("mcp_policy_blocked", server=server, actor_id=actor_id, duration_ms=0, error_code=exc.code)
            raise
        started = perf_counter()
        try:
            async with self.guard.reserve(
                key=self._guard_key(server),
                max_concurrency=server.max_concurrency,
                daily_call_limit=server.daily_call_limit,
                consume_budget=False,
                failure_threshold=server.circuit_failure_threshold,
                reset_seconds=server.circuit_reset_seconds,
                lease_seconds=server.request_timeout_seconds + 5,
            ):
                tools = await asyncio.wait_for(
                    self.transport.discover(server, token),
                    timeout=server.request_timeout_seconds,
                )
        except McpGuardError as exc:
            raise self._from_guard(exc) from exc
        except TimeoutError:
            await self._record_failure(server)
            code = "E_MCP_TIMEOUT"
            updated = self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=code)
            self._audit("mcp_health_checked", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=code)
            return updated
        except Exception:
            await self._record_failure(server)
            code = "E_MCP_UPSTREAM"
            updated = self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=code)
            self._audit("mcp_health_checked", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=code)
            return updated
        await self._record_success(server)
        latency_ms = int((perf_counter() - started) * 1000)
        policy_error = self._catalog_policy_error(server, tools)
        updated = self.registry.record_runtime_status(
            server.id,
            tenant_id,
            status="policy_blocked" if policy_error else "healthy",
            latency_ms=latency_ms,
            error_code=policy_error,
            discovered_tools=self._discovered_names(tools),
            checked_at=checked_at,
        )
        self._audit("mcp_health_checked", server=server, actor_id=actor_id, duration_ms=latency_ms, error_code=policy_error)
        return updated

    async def invoke_tool(
        self,
        server_id: str,
        tenant_id: str,
        *,
        actor_id: str,
        tool_name: str,
        arguments: dict,
    ) -> dict:
        server = self._server(server_id, tenant_id)
        if not server.enabled:
            self._audit("mcp_call_blocked", server=server, actor_id=actor_id, duration_ms=0, error_code="E_MCP_DISABLED", tool_name=tool_name)
            raise McpInvocationError("E_MCP_DISABLED", "MCP Server 已停用", status_code=409)
        if tool_name not in server.allowed_tools:
            self._audit("mcp_call_blocked", server=server, actor_id=actor_id, duration_ms=0, error_code="E_MCP_TOOL_FORBIDDEN", tool_name=tool_name)
            raise McpInvocationError("E_MCP_TOOL_FORBIDDEN", "工具不在当前 Server 白名单内", status_code=403)
        try:
            encoded_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            self._audit("mcp_call_blocked", server=server, actor_id=actor_id, duration_ms=0, error_code="E_MCP_ARGUMENTS_INVALID", tool_name=tool_name)
            raise McpInvocationError("E_MCP_ARGUMENTS_INVALID", "工具参数必须是可序列化 JSON 对象", status_code=422) from exc
        if len(encoded_arguments) > settings.mcp_max_arguments_bytes:
            self._audit("mcp_call_blocked", server=server, actor_id=actor_id, duration_ms=0, error_code="E_MCP_ARGUMENTS_TOO_LARGE", tool_name=tool_name)
            raise McpInvocationError("E_MCP_ARGUMENTS_TOO_LARGE", "工具参数超过大小限制", status_code=413)
        try:
            token = self._credential(server)
            await self._validate_target(server)
        except McpInvocationError as exc:
            self._audit("mcp_call_blocked", server=server, actor_id=actor_id, duration_ms=0, error_code=exc.code, tool_name=tool_name)
            raise
        started = perf_counter()
        discovered = []
        selected_tool = []

        def validate_tools(tools):
            discovered[:] = list(tools)
            descriptor = self._tool_map(discovered).get(tool_name)
            selected_tool[:] = [descriptor] if descriptor else []
            self._validate_tool(discovered, tool_name)

        try:
            async with self.guard.reserve(
                key=self._guard_key(server),
                max_concurrency=server.max_concurrency,
                daily_call_limit=server.daily_call_limit,
                consume_budget=True,
                failure_threshold=server.circuit_failure_threshold,
                reset_seconds=server.circuit_reset_seconds,
                lease_seconds=server.request_timeout_seconds + 5,
            ):
                result = await asyncio.wait_for(
                    self.transport.execute(server, token, tool_name, arguments, validate_tools),
                    timeout=server.request_timeout_seconds,
                )
        except McpGuardError as exc:
            error = self._from_guard(exc)
            self._audit("mcp_call_blocked", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=error.code, tool_name=tool_name)
            raise error from exc
        except McpInvocationError as exc:
            await self._record_success(server)
            self.registry.record_runtime_status(
                server.id,
                tenant_id,
                status="policy_blocked",
                error_code=exc.code,
                discovered_tools=self._discovered_names(discovered),
            )
            self._audit("mcp_policy_blocked", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=exc.code, tool_name=tool_name)
            raise
        except TimeoutError as exc:
            await self._record_failure(server)
            error = McpInvocationError("E_MCP_TIMEOUT", "外部 MCP 调用超时", status_code=504, retryable=True)
            self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=error.code)
            self._audit("mcp_tool_failed", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=error.code, tool_name=tool_name)
            raise error from exc
        except Exception as exc:
            await self._record_failure(server)
            error = McpInvocationError("E_MCP_UPSTREAM", "外部 MCP 调用失败", status_code=502, retryable=True)
            self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=error.code)
            self._audit("mcp_tool_failed", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=error.code, tool_name=tool_name)
            raise error from exc
        if not selected_tool:
            await self._record_failure(server)
            error = McpInvocationError(
                "E_MCP_TOOL_CATALOG_INVALID",
                "外部 MCP 未提供经过验证的工具描述",
                status_code=502,
            )
            self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=error.code)
            self._audit(
                "mcp_tool_failed",
                server=server,
                actor_id=actor_id,
                duration_ms=int((perf_counter() - started) * 1000),
                error_code=error.code,
                tool_name=tool_name,
            )
            raise error
        try:
            encoded_result = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            await self._record_failure(server)
            error = McpInvocationError("E_MCP_RESULT_INVALID", "外部 MCP 返回了无效结果", status_code=502, retryable=True)
            self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=error.code)
            self._audit("mcp_tool_failed", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=error.code, tool_name=tool_name)
            raise error from exc
        if len(encoded_result) > settings.mcp_max_result_bytes:
            await self._record_failure(server)
            error = McpInvocationError("E_MCP_RESULT_TOO_LARGE", "外部 MCP 结果超过大小限制", status_code=502)
            self.registry.record_runtime_status(server.id, tenant_id, status="unhealthy", error_code=error.code)
            self._audit("mcp_tool_failed", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=error.code, tool_name=tool_name)
            raise error
        if result.get("resultType") == "input_required":
            await self._record_success(server)
            error = McpInvocationError("E_MCP_INTERACTION_REQUIRED", "外部工具要求交互输入，当前调用模式不支持", status_code=409)
            self.registry.record_runtime_status(
                server.id,
                tenant_id,
                status="policy_blocked",
                error_code=error.code,
                discovered_tools=self._discovered_names(discovered),
            )
            self._audit("mcp_policy_blocked", server=server, actor_id=actor_id, duration_ms=int((perf_counter() - started) * 1000), error_code=error.code, tool_name=tool_name)
            raise error
        await self._record_success(server)
        latency_ms = int((perf_counter() - started) * 1000)
        replay_policy = self.replay_policies.for_mcp_tool(selected_tool[0])
        self.registry.record_runtime_status(
            server.id,
            tenant_id,
            status="healthy",
            latency_ms=latency_ms,
            discovered_tools=self._discovered_names(discovered),
        )
        self._audit(
            "mcp_tool_returned_error" if result.get("isError") else "mcp_tool_called",
            server=server,
            actor_id=actor_id,
            duration_ms=latency_ms,
            tool_name=tool_name,
            replay_policy=replay_policy,
        )
        return {"server_id": server.id, "tool_name": tool_name, "duration_ms": latency_ms, "replay_policy": replay_policy.as_dict(), "result": result}


__all__ = ["McpInvocationError", "McpInvocationService", "McpToolDescriptor"]
