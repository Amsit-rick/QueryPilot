"""Data task use cases independent of HTTP and database implementations."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.domain.agent_recovery import RecoveryLeaseError
from app.domain.task import DataTask, TaskArtifact, TaskEvent, TaskStep
from app.repositories.protocols import TaskRepository


class TaskService:
    def __init__(self, repository: TaskRepository):
        self.repository = repository

    def create(
        self,
        *,
        question: str,
        tenant_id: str,
        owner_id: str,
        datasource_id: str,
        title: str = "",
        task_id: str | None = None,
        parent_task_id: str = "",
        root_task_id: str = "",
        branch_source_trace_id: str = "",
        branch_label: str = "",
        branch_depth: int = 0,
    ) -> DataTask:
        question = question.strip()
        if not question:
            raise ValueError("E_TASK_QUESTION_EMPTY:任务问题不能为空")
        values = {
            "question": question,
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "datasource_id": datasource_id,
            "title": title,
            "parent_task_id": parent_task_id,
            "root_task_id": root_task_id,
            "branch_source_trace_id": branch_source_trace_id,
            "branch_label": branch_label,
            "branch_depth": int(branch_depth),
        }
        if task_id is not None:
            values["id"] = task_id
        task = self.repository.create(DataTask(**values))
        self.repository.add_event(TaskEvent(task_id=task.id, kind="task_created", payload={"status": task.status}))
        return task

    def transition(self, task_id: str, tenant_id: str, status: str) -> DataTask:
        stored = self.repository.get(task_id, tenant_id)
        if stored is None:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        task = replace(stored)
        previous_status = task.status
        task.transition(status)
        updated = self.repository.update(task)
        self.repository.add_event(TaskEvent(task_id=task.id, kind="status_changed", payload={"from": previous_status, "to": status}))
        return updated

    def begin_follow_up(self, task_id: str, tenant_id: str) -> DataTask:
        task = self.repository.get(task_id, tenant_id)
        if task is None:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        previous_status = task.status
        task.begin_follow_up()
        updated = self.repository.update(task)
        self.repository.add_event(TaskEvent(task_id=task.id, kind="status_changed", payload={"from": previous_status, "to": task.status, "reason": "follow_up"}))
        return updated

    def restore_completed_after_follow_up(self, task_id: str, tenant_id: str, *, reason: str) -> DataTask:
        task = self.repository.get(task_id, tenant_id)
        if task is None:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        previous_status = task.status
        task.restore_completed_after_follow_up()
        updated = self.repository.update(task)
        self.repository.add_event(TaskEvent(task_id=task.id, kind="status_changed", payload={"from": previous_status, "to": task.status, "reason": reason}))
        return updated

    def append_step(self, *, task_id: str, kind: str, sequence: int, status: str = "pending", input: dict | None = None, output: dict | None = None, error: str = "", duration_ms: int = 0, retry_count: int = 0) -> TaskStep:
        if sequence < 0:
            raise ValueError("E_TASK_STEP_SEQUENCE:步骤序号不能为负数")
        if duration_ms < 0 or retry_count < 0:
            raise ValueError("E_TASK_STEP_METRICS:步骤耗时和重试次数不能为负数")
        return self.repository.add_step(TaskStep(task_id=task_id, kind=kind, sequence=sequence, status=status, input=input or {}, output=output or {}, error=error, duration_ms=duration_ms, retry_count=retry_count))

    def append_event(self, *, task_id: str, kind: str, payload: dict | None = None) -> TaskEvent:
        return self.repository.add_event(TaskEvent(task_id=task_id, kind=kind, payload=payload or {}))

    def resolve_approval_step(self, *, task_id: str, status: str, output: dict | None = None, error: str = "") -> TaskStep | None:
        return self.repository.update_latest_step(task_id, "approval", status, output or {}, error)

    def add_artifact(self, *, task_id: str, kind: str, name: str, uri: str = "", metadata: dict | None = None) -> TaskArtifact:
        if not name.strip():
            raise ValueError("E_TASK_ARTIFACT_NAME:结果资产名称不能为空")
        return self.repository.add_artifact(TaskArtifact(task_id=task_id, kind=kind, name=name.strip(), uri=uri, metadata=metadata or {}))

    def ensure_artifact(self, *, task_id: str, kind: str, name: str, idempotency_key: str, uri: str = "", metadata: dict | None = None) -> TaskArtifact:
        for artifact in self.repository.list_artifacts(task_id):
            if artifact.metadata.get("idempotency_key") == idempotency_key:
                return artifact
        payload = {**(metadata or {}), "idempotency_key": idempotency_key}
        return self.add_artifact(task_id=task_id, kind=kind, name=name, uri=uri, metadata=payload)

    def detail(self, *, task_id: str, tenant_id: str, owner_id: str) -> dict:
        task = self.repository.get(task_id, tenant_id)
        if task is None or task.owner_id != owner_id:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        return {
            "task": task,
            "steps": self.repository.list_steps(task_id),
            "events": self.repository.list_events(task_id),
            "artifacts": self.repository.list_artifacts(task_id),
        }

    def detail_for_tenant(self, *, task_id: str, tenant_id: str) -> dict:
        task = self.repository.get(task_id, tenant_id)
        if task is None:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        return {
            "task": task,
            "steps": self.repository.list_steps(task_id),
            "events": self.repository.list_events(task_id),
            "artifacts": self.repository.list_artifacts(task_id),
        }

    def list_artifacts_for_tenant(self, *, tenant_id: str, kind: str, limit: int = 1000) -> list[TaskArtifact]:
        return self.repository.list_artifacts_for_tenant(tenant_id, kind, limit)

    def list(self, *, tenant_id: str, owner_id: str, limit: int = 20) -> list[DataTask]:
        return self.repository.list(tenant_id, owner_id, max(1, min(limit, 100)))

    def list_branches(self, *, parent_task_id: str, tenant_id: str, owner_id: str) -> list[DataTask]:
        return self.repository.list_branches(
            parent_task_id=parent_task_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    def branch_family(self, *, task_id: str, tenant_id: str, owner_id: str) -> list[DataTask]:
        task = self.repository.get(task_id, tenant_id)
        if task is None or task.owner_id != owner_id:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        root_task_id = task.root_task_id or task.id
        return self.repository.list_branch_family(
            root_task_id=root_task_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    def list_recoverable(
        self,
        *,
        statuses: tuple[str, ...],
        updated_before: datetime,
        limit: int = 50,
    ) -> list[DataTask]:
        return self.repository.list_recoverable(
            statuses=statuses,
            updated_before=updated_before,
            limit=max(1, min(int(limit), 500)),
        )

    def claim_recovery_fence(
        self,
        *,
        task_id: str,
        tenant_id: str,
        fencing_token: int,
    ) -> bool:
        if int(fencing_token) <= 0:
            raise RecoveryLeaseError("E_RECOVERY_FENCING_INVALID", "fencing token 必须为正整数")
        return self.repository.claim_recovery_fence(
            task_id=task_id,
            tenant_id=tenant_id,
            fencing_token=int(fencing_token),
        )

    def transition_recovered(
        self,
        *,
        task_id: str,
        tenant_id: str,
        status: str,
        fencing_token: int,
    ) -> DataTask:
        stored = self.repository.get(task_id, tenant_id)
        if stored is None:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        task = replace(stored)
        previous_status = task.status
        task.transition(status)
        updated = self.repository.update_recovery_status(
            task=task,
            fencing_token=int(fencing_token),
        )
        if updated is None:
            raise RecoveryLeaseError("E_RECOVERY_FENCING_STALE", "旧恢复实例不能提交任务状态")
        self.repository.add_event(
            TaskEvent(
                task_id=task.id,
                kind="status_changed",
                payload={
                    "from": previous_status,
                    "to": status,
                    "reason": "recovery_supervisor",
                    "fencing_token": int(fencing_token),
                },
            )
        )
        return updated

    def append_recovery_event(
        self,
        *,
        task_id: str,
        tenant_id: str,
        fencing_token: int,
        idempotency_key: str,
        payload: dict,
    ) -> bool:
        return self.repository.add_recovery_event(
            task_id=task_id,
            tenant_id=tenant_id,
            fencing_token=int(fencing_token),
            idempotency_key=idempotency_key,
            payload=payload,
        )
