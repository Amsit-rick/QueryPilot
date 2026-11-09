"""Versioned snapshot and delta contracts for resumable query streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DURABLE_STREAM_VERSION = "2"
TERMINAL_STREAM_EVENTS = {"completed", "error", "approval_required"}
_FORBIDDEN_KEYS = {
    "params",
    "provided_params",
    "raw_response",
    "model_response",
    "api_key",
    "password",
    "secret",
    "token",
}


def sanitize_stream_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize_stream_data(item)
            for key, item in value.items()
            if str(key).lower() not in _FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [sanitize_stream_data(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_stream_data(item) for item in value]
    return value


def initial_stream_snapshot() -> dict[str, Any]:
    return {
        "status": "running",
        "terminal": False,
        "task_id": "",
        "result": None,
        "rows": [],
        "progress": None,
        "completion": None,
        "error": None,
        "approval": None,
        "last_sequence": 0,
    }


def apply_stream_event(
    snapshot: dict[str, Any],
    *,
    sequence: int,
    event: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    state = sanitize_stream_data(snapshot)
    safe_data = sanitize_stream_data(data)
    state["last_sequence"] = int(sequence)
    if event == "started":
        state["status"] = "running"
    elif event == "progress":
        state["progress"] = safe_data
    elif event == "result":
        state["status"] = "streaming"
        state["task_id"] = str(safe_data.get("task_id") or "")
        state["result"] = safe_data
        state["rows"] = []
    elif event == "rows":
        rows = safe_data.get("rows")
        if isinstance(rows, list):
            state["rows"] = [*state.get("rows", []), *rows]
    elif event == "completed":
        state["status"] = "completed"
        state["terminal"] = True
        state["completion"] = safe_data
    elif event == "error":
        state["status"] = "failed"
        state["terminal"] = True
        state["task_id"] = str(safe_data.get("task_id") or state.get("task_id") or "")
        state["error"] = safe_data
    elif event == "approval_required":
        state["status"] = "approval_required"
        state["terminal"] = True
        state["task_id"] = str(safe_data.get("task_id") or state.get("task_id") or "")
        state["approval"] = safe_data
    return state


@dataclass(frozen=True)
class QueryStreamScope:
    tenant_id: str
    owner_id: str
    operation: str
    client_request_id: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (
            self.tenant_id,
            self.owner_id,
            self.operation,
            self.client_request_id,
        )


@dataclass(frozen=True)
class QueryStreamEvent:
    stream_id: str
    sequence: int
    event: str
    data: dict[str, Any]


@dataclass
class QueryStreamSession:
    id: str
    scope: QueryStreamScope
    snapshot: dict[str, Any] = field(default_factory=initial_stream_snapshot)
    last_sequence: int = 0
    first_sequence: int = 1
    terminal: bool = False
    expires_at: float = 0


__all__ = [
    "DURABLE_STREAM_VERSION",
    "TERMINAL_STREAM_EVENTS",
    "QueryStreamEvent",
    "QueryStreamScope",
    "QueryStreamSession",
    "apply_stream_event",
    "initial_stream_snapshot",
    "sanitize_stream_data",
]
