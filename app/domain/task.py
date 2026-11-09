"""Domain objects for durable data tasks."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class DataTask:
    """A user-owned, replayable unit of data analysis."""

    question: str
    tenant_id: str = "default"
    owner_id: str = ""
    datasource_id: str = ""
    parent_task_id: str = ""
    root_task_id: str = ""
    branch_source_trace_id: str = ""
    branch_label: str = ""
    branch_depth: int = 0
    title: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "draft"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    _transitions = {
        "draft": {"planning", "cancelled"},
        "planning": {"running", "failed", "cancelled"},
        "running": {"verifying", "approval_required", "clarification_required", "completed", "failed", "cancelled"},
        "verifying": {"running", "completed", "failed"},
        "approval_required": {"running", "cancelled", "failed"},
        "clarification_required": {"planning", "cancelled", "failed"},
        "completed": set(),
        "failed": {"planning", "running"},
        "cancelled": set(),
    }

    def display_title(self) -> str:
        return (self.title or self.question).strip()[:120]

    def transition(self, status: str) -> None:
        if status not in self._transitions.get(self.status, set()):
            raise ValueError(f"E_TASK_TRANSITION:不能从 {self.status} 转换到 {status}")
        self.status = status
        self.updated_at = datetime.now(timezone.utc)

    def begin_follow_up(self) -> None:
        if self.status != "completed":
            raise ValueError(f"E_TASK_FOLLOW_UP_STATE:任务当前状态为 {self.status}，只有已完成任务可以追问")
        self.status = "planning"
        self.updated_at = datetime.now(timezone.utc)

    def restore_completed_after_follow_up(self) -> None:
        if self.status not in {"planning", "running", "verifying", "approval_required"}:
            raise ValueError(f"E_TASK_FOLLOW_UP_RESTORE:任务当前状态为 {self.status}，不能恢复追问前状态")
        self.status = "completed"
        self.updated_at = datetime.now(timezone.utc)


@dataclass
class TaskStep:
    """An auditable step within a DataTask."""

    task_id: str
    kind: str
    status: str = "pending"
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    sequence: int = 0
    id: int | None = None
    duration_ms: int = 0
    retry_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TaskEvent:
    """An immutable lifecycle or audit event attached to a task."""

    task_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TaskArtifact:
    """A durable result produced by a task, such as a table or CSV."""

    task_id: str
    kind: str
    name: str
    uri: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
