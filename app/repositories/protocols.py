"""Repository protocols; concrete database implementations stay in infrastructure."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from app.domain.task import DataTask, TaskArtifact, TaskEvent, TaskStep


class TaskRepository(Protocol):
    def create(self, task: DataTask) -> DataTask: ...
    def get(self, task_id: str, tenant_id: str) -> DataTask | None: ...
    def add_step(self, step: TaskStep) -> TaskStep: ...
    def list_steps(self, task_id: str) -> list[TaskStep]: ...
    def update_latest_step(self, task_id: str, kind: str, status: str, output: dict, error: str = "") -> TaskStep | None: ...
    def add_event(self, event: TaskEvent) -> TaskEvent: ...
    def list_events(self, task_id: str) -> list[TaskEvent]: ...
    def add_artifact(self, artifact: TaskArtifact) -> TaskArtifact: ...
    def list_artifacts(self, task_id: str) -> list[TaskArtifact]: ...
    def list_artifacts_for_tenant(self, tenant_id: str, kind: str, limit: int = 1000) -> list[TaskArtifact]: ...

    def update(self, task: DataTask) -> DataTask: ...

    def list(self, tenant_id: str, owner_id: str, limit: int = 20) -> list[DataTask]: ...

    def list_branches(self, *, parent_task_id: str, tenant_id: str, owner_id: str) -> list[DataTask]: ...

    def list_branch_family(self, *, root_task_id: str, tenant_id: str, owner_id: str) -> list[DataTask]: ...

    def list_recoverable(
        self,
        *,
        statuses: tuple[str, ...],
        updated_before: datetime,
        limit: int,
    ) -> list[DataTask]: ...

    def claim_recovery_fence(
        self,
        *,
        task_id: str,
        tenant_id: str,
        fencing_token: int,
    ) -> bool: ...

    def update_recovery_status(
        self,
        *,
        task: DataTask,
        fencing_token: int,
    ) -> DataTask | None: ...

    def add_recovery_event(
        self,
        *,
        task_id: str,
        tenant_id: str,
        fencing_token: int,
        idempotency_key: str,
        payload: dict,
    ) -> bool: ...
