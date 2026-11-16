"""Bounded external MCP enrichment for the query Agent.

The service deliberately keeps MCP outside the database execution boundary.
It selects a small, fixed set of read-only semantic tools, treats every result
as untrusted data, and returns only sanitized context plus redacted evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from time import perf_counter
from typing import Any

from .mcp_invocation_service import McpInvocationError
from .prompt_injection_service import sanitize_untrusted_context


AGENT_MCP_TOOLS = frozenset(
    {"catalog.search", "metric.lookup", "lineage.get", "policy.explain"}
)

_ROUTE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "policy.explain",
        re.compile(r"权限|脱敏|审批|访问策略|安全策略|行级|列级|policy|permission|mask", re.I),
    ),
    (
        "lineage.get",
        re.compile(r"血缘|上游|下游|主外键|外键|关系链|来源表|lineage|upstream|downstream", re.I),
    ),
    (
        "metric.lookup",
        re.compile(r"指标|统计口径|业务口径|口径是什么|gmv|转化率|复购率|metric", re.I),
    ),
    (
        "catalog.search",
        re.compile(r"在哪张表|哪个表|哪些字段|哪个字段|字段在哪|数据字典|表结构|catalog|schema", re.I),
    ),
)

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|token|secret|password|passwd)\s*[:=]\s*[^\s,，;；]{6,}"
)
_LONG_NUMBER = re.compile(r"(?<!\d)\d{12,}(?!\d)")


@dataclass(frozen=True)
class McpEnrichmentLimits:
    max_tools: int = 2
    max_result_bytes: int = 32 * 1024
    max_depth: int = 8
    max_fields: int = 256
    max_text_chars: int = 2000
    max_question_chars: int = 512

    def __post_init__(self) -> None:
        for field_name in (
            "max_tools",
            "max_result_bytes",
            "max_depth",
            "max_fields",
            "max_text_chars",
            "max_question_chars",
        ):
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True)
class McpEnrichmentOutcome:
    context: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]


class McpAgentResultError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def route_agent_mcp_tools(question: str, *, max_tools: int) -> tuple[str, ...]:
    """Select only fixed semantic tools using deterministic, reviewable rules."""
    if max_tools <= 0:
        return ()
    normalized = " ".join(str(question or "").split())[:4000]
    matched = [tool for tool, pattern in _ROUTE_PATTERNS if pattern.search(normalized)]
    if not matched:
        return ()
    # Catalog terms are often incidental to stronger policy/lineage/metric
    # intents. Avoid an unnecessary second external call in that case.
    if matched[0] != "catalog.search":
        matched = [tool for tool in matched if tool != "catalog.search"]
    return tuple(dict.fromkeys(matched))[:max_tools]


def _redact_question(question: str, *, max_chars: int) -> str:
    value = " ".join(str(question or "").split())[: max_chars * 2]
    value = _EMAIL.sub("[redacted-email]", value)
    value = _PHONE.sub("[redacted-phone]", value)
    value = _SECRET.sub(lambda match: f"{match.group(1)}=[redacted-secret]", value)
    value = _LONG_NUMBER.sub("[redacted-id]", value)
    return value[:max_chars]


def _json_size(value: Any) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise McpAgentResultError("E_MCP_AGENT_RESULT_INVALID", "外部 MCP 返回结果不是有效 JSON") from exc


def _validate_shape(value: Any, *, limits: McpEnrichmentLimits) -> None:
    fields = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal fields
        if depth > limits.max_depth:
            raise McpAgentResultError("E_MCP_AGENT_RESULT_DEPTH", "外部 MCP 返回结果嵌套过深")
        if isinstance(item, dict):
            fields += len(item)
            if fields > limits.max_fields:
                raise McpAgentResultError("E_MCP_AGENT_RESULT_FIELDS", "外部 MCP 返回字段过多")
            for key, child in item.items():
                if not isinstance(key, str):
                    raise McpAgentResultError("E_MCP_AGENT_RESULT_INVALID", "外部 MCP 返回对象键必须是字符串")
                walk(child, depth + 1)
            return
        if isinstance(item, (list, tuple)):
            fields += len(item)
            if fields > limits.max_fields:
                raise McpAgentResultError("E_MCP_AGENT_RESULT_FIELDS", "外部 MCP 返回字段过多")
            for child in item:
                walk(child, depth + 1)
            return
        if isinstance(item, float) and not math.isfinite(item):
            raise McpAgentResultError("E_MCP_AGENT_RESULT_INVALID", "外部 MCP 返回非有限数值")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise McpAgentResultError("E_MCP_AGENT_RESULT_INVALID", "外部 MCP 返回不支持的数据类型")

    walk(value, 0)


def _truncate_strings(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, dict):
        return {str(key)[:128]: _truncate_strings(child, max_chars=max_chars) for key, child in value.items()}
    if isinstance(value, list):
        return [_truncate_strings(child, max_chars=max_chars) for child in value]
    if isinstance(value, tuple):
        return [_truncate_strings(child, max_chars=max_chars) for child in value]
    if isinstance(value, str) and len(value) > max_chars:
        return f"{value[:max_chars]}…"
    return value


def sanitize_agent_mcp_result(
    value: Any,
    *,
    tool: str,
    limits: McpEnrichmentLimits,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if not isinstance(value, dict):
        raise McpAgentResultError("E_MCP_AGENT_RESULT_INVALID", "外部 MCP 返回根节点必须是对象")
    if _json_size(value) > limits.max_result_bytes:
        raise McpAgentResultError("E_MCP_AGENT_RESULT_TOO_LARGE", "外部 MCP 返回结果超过 Agent 上下文限制")
    _validate_shape(value, limits=limits)
    if value.get("isError") is True:
        raise McpAgentResultError("E_MCP_AGENT_UPSTREAM_RESULT", "外部 MCP 工具返回错误结果")
    bounded = _truncate_strings(value, max_chars=limits.max_text_chars)
    sanitized, findings = sanitize_untrusted_context(bounded, source=f"external_mcp:{tool}")
    encoded = json.dumps(
        sanitized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sanitized, findings, hashlib.sha256(encoded).hexdigest()


class McpAgentEnrichmentService:
    def __init__(self, *, registry, invocation, enabled: bool, limits: McpEnrichmentLimits):
        self.registry = registry
        self.invocation = invocation
        self.enabled = bool(enabled)
        self.limits = limits

    @staticmethod
    def _can_use_datasource(identity: dict[str, Any], datasource: dict[str, Any]) -> bool:
        roles = {str(role) for role in (identity.get("roles") or [])}
        allowed_roles = {str(role) for role in (datasource.get("roles") or [])}
        return "admin" in roles or bool(roles & allowed_roles)

    @staticmethod
    def _evidence(
        *,
        server_id: str,
        tool: str,
        status: str,
        duration_ms: int = 0,
        error_code: str = "",
        result_sha256: str = "",
        security_findings: int = 0,
    ) -> dict[str, Any]:
        return {
            "server_id": str(server_id)[:128],
            "tool": str(tool)[:64],
            "status": "completed" if status == "completed" else "degraded",
            "duration_ms": max(0, int(duration_ms)),
            "error_code": str(error_code)[:80],
            "result_sha256": str(result_sha256)[:64],
            "security_findings": max(0, int(security_findings)),
        }

    def _select_server(self, tenant_id: str, tool: str):
        candidates = [
            server
            for server in self.registry.list_servers(tenant_id)
            if bool(getattr(server, "enabled", False))
            and tool in tuple(getattr(server, "allowed_tools", ()))
        ]
        return min(
            candidates,
            key=lambda server: (str(getattr(server, "name", "")).casefold(), str(server.id)),
            default=None,
        )

    async def enrich(
        self,
        question: str,
        identity: dict[str, Any],
        datasource: dict[str, Any],
    ) -> McpEnrichmentOutcome:
        if not self.enabled or not self._can_use_datasource(identity, datasource):
            return McpEnrichmentOutcome(context={}, evidence=())
        tools = route_agent_mcp_tools(question, max_tools=self.limits.max_tools)
        if not tools:
            return McpEnrichmentOutcome(context={}, evidence=())

        tenant_id = str(identity.get("tenant_id") or "default")
        actor_id = str(identity.get("subject") or "anonymous")
        arguments = {
            "question_summary": _redact_question(
                question,
                max_chars=self.limits.max_question_chars,
            ),
            "datasource": {
                "id": str(datasource.get("id") or "")[:128],
                "backend": str(datasource.get("backend") or "")[:64],
            },
        }
        items: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        for tool in tools:
            if tool not in AGENT_MCP_TOOLS:
                continue
            server = self._select_server(tenant_id, tool)
            if server is None:
                evidence.append(
                    self._evidence(
                        server_id="",
                        tool=tool,
                        status="degraded",
                        error_code="E_MCP_AGENT_TOOL_UNAVAILABLE",
                    )
                )
                continue
            started = perf_counter()
            try:
                response = await self.invocation.invoke_tool(
                    server.id,
                    tenant_id,
                    actor_id=actor_id,
                    tool_name=tool,
                    arguments=arguments,
                )
                sanitized, findings, result_sha256 = sanitize_agent_mcp_result(
                    response.get("result"),
                    tool=tool,
                    limits=self.limits,
                )
            except McpInvocationError as exc:
                evidence.append(
                    self._evidence(
                        server_id=server.id,
                        tool=tool,
                        status="degraded",
                        duration_ms=int((perf_counter() - started) * 1000),
                        error_code=exc.code,
                    )
                )
                continue
            except McpAgentResultError as exc:
                evidence.append(
                    self._evidence(
                        server_id=server.id,
                        tool=tool,
                        status="degraded",
                        duration_ms=int(response.get("duration_ms", 0) or 0),
                        error_code=exc.code,
                    )
                )
                continue
            except Exception:
                evidence.append(
                    self._evidence(
                        server_id=server.id,
                        tool=tool,
                        status="degraded",
                        duration_ms=int((perf_counter() - started) * 1000),
                        error_code="E_MCP_AGENT_INTERNAL",
                    )
                )
                continue

            items.append(
                {
                    "server_id": str(server.id)[:128],
                    "tool": tool,
                    "trust": "untrusted_external_data",
                    "data": sanitized,
                }
            )
            evidence.append(
                self._evidence(
                    server_id=server.id,
                    tool=tool,
                    status="completed",
                    duration_ms=int(response.get("duration_ms", 0) or 0),
                    result_sha256=result_sha256,
                    security_findings=len(findings),
                )
            )

        context = (
            {
                "version": 1,
                "trust": "untrusted_external_data",
                "items": items,
            }
            if items
            else {}
        )
        return McpEnrichmentOutcome(context=context, evidence=tuple(evidence))


__all__ = [
    "AGENT_MCP_TOOLS",
    "McpAgentEnrichmentService",
    "McpEnrichmentLimits",
    "McpEnrichmentOutcome",
    "route_agent_mcp_tools",
    "sanitize_agent_mcp_result",
]
