"""Versioned, secret-safe contracts for resumable agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CHECKPOINT_SCHEMA_VERSION = 1
_FORBIDDEN_SNAPSHOT_KEYS = {
    "params",
    "provided_params",
    "result",
    "results",
    "rows",
    "raw_response",
    "model_response",
}


class RecoveryLeaseError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}:{message}")


def _sanitize_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_snapshot(item)
            for key, item in value.items()
            if str(key).lower() not in _FORBIDDEN_SNAPSHOT_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_snapshot(item) for item in value]
    return value


@dataclass(frozen=True)
class RecoveryLease:
    task_id: str
    tenant_id: str
    holder_id: str
    fencing_token: int
    expires_at: float


@dataclass(frozen=True)
class AgentCheckpoint:
    schema_version: int
    task_id: str
    tenant_id: str
    execution_id: str
    node_cursor: str
    fencing_token: int
    snapshot: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        tenant_id: str,
        execution_id: str,
        node_cursor: str,
        fencing_token: int,
        snapshot: dict[str, Any],
    ) -> "AgentCheckpoint":
        return cls(
            schema_version=CHECKPOINT_SCHEMA_VERSION,
            task_id=str(task_id),
            tenant_id=str(tenant_id),
            execution_id=str(execution_id),
            node_cursor=str(node_cursor),
            fencing_token=int(fencing_token),
            snapshot=_sanitize_snapshot(snapshot),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentCheckpoint":
        schema_version = int(value.get("schema_version", 0) or 0)
        if schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise RecoveryLeaseError(
                "E_CHECKPOINT_SCHEMA_UNSUPPORTED",
                "checkpoint 版本不受支持，已拒绝恢复",
            )
        return cls.create(
            task_id=value.get("task_id", ""),
            tenant_id=value.get("tenant_id", ""),
            execution_id=value.get("execution_id", ""),
            node_cursor=value.get("node_cursor", ""),
            fencing_token=value.get("fencing_token", 0),
            snapshot=value.get("snapshot", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "execution_id": self.execution_id,
            "node_cursor": self.node_cursor,
            "fencing_token": self.fencing_token,
            "snapshot": _sanitize_snapshot(self.snapshot),
        }


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "AgentCheckpoint",
    "RecoveryLease",
    "RecoveryLeaseError",
]
