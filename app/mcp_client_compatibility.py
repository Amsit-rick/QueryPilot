"""Reproducible MCP client compatibility probes and conservative matrix output."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import anyio


CHECK_STATUSES = frozenset({"passed", "limited", "failed"})
CLIENT_STATUSES = frozenset({"verified", "limited", "failed", "not_tested"})
EXPECTED_OFFICIAL_TRANSPORTS = ("stdio", "streamable-http")


@dataclass(frozen=True)
class CompatibilityCheck:
    name: str
    status: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in CHECK_STATUSES:
            raise ValueError(f"unsupported compatibility check status: {self.status}")


@dataclass(frozen=True)
class TransportVerification:
    client: str
    client_version: str
    transport: str
    checks: tuple[CompatibilityCheck, ...]
    limitations: tuple[str, ...] = ()


UNVERIFIED_CLIENTS: tuple[dict[str, Any], ...] = (
    {
        "client": "claude-code",
        "client_version": None,
        "transport": "stdio / streamable-http",
        "status": "not_tested",
        "checks": {},
        "limitations": ["未用固定版本 Claude Code 执行 Tools、Resource、错误码和取消全链路验收。"],
    },
    {
        "client": "claude-desktop",
        "client_version": None,
        "transport": "stdio / streamable-http",
        "status": "not_tested",
        "checks": {},
        "limitations": ["本机未安装并执行固定版本的 Claude Desktop，不能声明兼容。"],
    },
    {
        "client": "cursor",
        "client_version": None,
        "transport": "stdio / streamable-http",
        "status": "not_tested",
        "checks": {},
        "limitations": ["本机未使用固定版本 Cursor 完成连接、资源和取消验收。"],
    },
    {
        "client": "codex",
        "client_version": None,
        "transport": "stdio / streamable-http",
        "status": "not_tested",
        "checks": {},
        "limitations": ["本轮未修改用户 Codex 配置，也未把当前 Codex App 会话当作协议验收证据。"],
    },
)


def _transport_status(checks: Iterable[CompatibilityCheck]) -> str:
    statuses = {check.status for check in checks}
    if not statuses:
        return "failed"
    if "failed" in statuses:
        return "failed"
    if "limited" in statuses:
        return "limited"
    return "verified"


def build_compatibility_matrix(
    *,
    sdk_version: str,
    protocol_version: str,
    verifications: tuple[TransportVerification, ...],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a matrix that distinguishes executed probes from named clients."""

    rows: list[dict[str, Any]] = []
    for verification in verifications:
        check_map = {
            check.name: {"status": check.status, "detail": check.detail}
            for check in verification.checks
        }
        rows.append(
            {
                "client": verification.client,
                "client_version": verification.client_version,
                "transport": verification.transport,
                "status": _transport_status(verification.checks),
                "checks": check_map,
                "limitations": list(verification.limitations),
            }
        )

    present_pairs = {(row["client"], row["transport"]) for row in rows}
    for transport in EXPECTED_OFFICIAL_TRANSPORTS:
        if ("official-python-sdk", transport) not in present_pairs:
            rows.append(
                {
                    "client": "official-python-sdk",
                    "client_version": sdk_version,
                    "transport": transport,
                    "status": "not_tested",
                    "checks": {},
                    "limitations": ["本次命令未执行该传输，不能沿用其他传输的验证结果。"],
                }
            )

    present_clients = {row["client"] for row in rows}
    rows.extend(dict(row) for row in UNVERIFIED_CLIENTS if row["client"] not in present_clients)
    summary = {status: 0 for status in ("verified", "limited", "failed", "not_tested")}
    for row in rows:
        status = row["status"]
        if status not in CLIENT_STATUSES:
            raise ValueError(f"unsupported compatibility client status: {status}")
        summary[status] += 1

    timestamp = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": 1,
        "generated_at": timestamp.isoformat(),
        "sdk": {"name": "mcp", "version": sdk_version},
        "protocol_version": protocol_version,
        "scope": (
            "Local protocol-level verification. Product clients marked not_tested were not executed."
        ),
        "clients": rows,
        "summary": summary,
    }


async def verify_client_cancellation(session: Any) -> CompatibilityCheck:
    """Verify that cancellation reaches a pending official-client request task."""

    observed = False
    cancelled_exc = anyio.get_cancelled_exc_class()

    async def pending_request() -> None:
        nonlocal observed
        try:
            await session.list_tools()
        except cancelled_exc:
            observed = True

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(pending_request)
        await anyio.sleep(0)
        task_group.cancel_scope.cancel()

    if observed:
        return CompatibilityCheck(
            "cancellation",
            "passed",
            "client request cancelled and transport context remained closable",
        )
    return CompatibilityCheck(
        "cancellation",
        "limited",
        "request completed before cancellation could be observed",
    )


__all__ = [
    "CompatibilityCheck",
    "TransportVerification",
    "build_compatibility_matrix",
    "verify_client_cancellation",
]
