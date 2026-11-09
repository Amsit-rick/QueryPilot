"""Secret-safe contracts for one bounded recovery supervisor pass."""

from __future__ import annotations

from dataclasses import dataclass, field


RECOVERABLE_TASK_STATUSES = (
    "planning",
    "running",
    "verifying",
    "approval_required",
)


@dataclass(frozen=True)
class RecoveryDecision:
    task_id: str
    tenant_id: str
    prior_status: str
    action: str
    status: str
    error_code: str = ""
    fencing_token: int = 0
    checkpoint_node: str = ""
    admission_status: str = ""
    replay_action: str = ""
    tool_name: str = ""


@dataclass(frozen=True)
class RecoveryBatchReport:
    scanned: int
    recovered: int
    skipped: int
    failed: int
    decisions: tuple[RecoveryDecision, ...] = field(default_factory=tuple)


__all__ = [
    "RECOVERABLE_TASK_STATUSES",
    "RecoveryBatchReport",
    "RecoveryDecision",
]
