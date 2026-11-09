"""Development-only task repository used when the app runs on SQLite."""
from datetime import timezone

from app.domain.task import DataTask, TaskArtifact, TaskEvent, TaskStep


class InMemoryTaskRepository:
    def __init__(self):
        self.tasks: dict[str, DataTask] = {}
        self.steps: list[TaskStep] = []
        self.events: list[TaskEvent] = []
        self.artifacts: list[TaskArtifact] = []
        self.recovery_fences: dict[tuple[str, str], int] = {}

    def create(self, task):
        self.tasks[task.id] = task
        return task

    def get(self, task_id, tenant_id):
        task = self.tasks.get(task_id)
        return task if task and task.tenant_id == tenant_id else None

    def update(self, task):
        self.tasks[task.id] = task
        return task

    def list(self, tenant_id, owner_id, limit=20):
        values = [t for t in self.tasks.values() if t.tenant_id == tenant_id and t.owner_id == owner_id]
        return sorted(values, key=lambda task: task.created_at, reverse=True)[:limit]

    def list_branches(self, *, parent_task_id, tenant_id, owner_id):
        values = [
            task
            for task in self.tasks.values()
            if task.tenant_id == tenant_id
            and task.owner_id == owner_id
            and task.parent_task_id == parent_task_id
        ]
        return sorted(values, key=lambda task: (task.created_at, task.id))

    def list_branch_family(self, *, root_task_id, tenant_id, owner_id):
        values = [
            task
            for task in self.tasks.values()
            if task.tenant_id == tenant_id
            and task.owner_id == owner_id
            and (task.id == root_task_id or task.root_task_id == root_task_id)
        ]
        return sorted(values, key=lambda task: (task.branch_depth, task.created_at, task.id))

    def list_recoverable(self, *, statuses, updated_before, limit):
        cutoff = updated_before
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

        def normalized(value):
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

        values = [
            task
            for task in self.tasks.values()
            if task.status in statuses and normalized(task.updated_at) <= cutoff
        ]
        return sorted(values, key=lambda task: (normalized(task.updated_at), task.id))[
            : max(1, min(int(limit), 500))
        ]

    def claim_recovery_fence(self, *, task_id, tenant_id, fencing_token):
        task = self.get(task_id, tenant_id)
        if task is None:
            return False
        key = (tenant_id, task_id)
        current = self.recovery_fences.get(key, 0)
        if int(fencing_token) <= current:
            return False
        self.recovery_fences[key] = int(fencing_token)
        return True

    def update_recovery_status(self, *, task, fencing_token):
        if self.recovery_fences.get((task.tenant_id, task.id), 0) != int(fencing_token):
            return None
        self.tasks[task.id] = task
        return task

    def add_recovery_event(
        self,
        *,
        task_id,
        tenant_id,
        fencing_token,
        idempotency_key,
        payload,
    ):
        if self.recovery_fences.get((tenant_id, task_id), 0) != int(fencing_token):
            return False
        if any(
            event.kind == "recovery_supervisor"
            and event.task_id == task_id
            and event.payload.get("idempotency_key") == idempotency_key
            for event in self.events
        ):
            return False
        self.events.append(
            TaskEvent(
                task_id=task_id,
                kind="recovery_supervisor",
                payload={**payload, "idempotency_key": idempotency_key},
            )
        )
        return True

    def add_step(self, step):
        self.steps.append(step)
        return step

    def list_steps(self, task_id):
        return sorted((step for step in self.steps if step.task_id == task_id), key=lambda step: step.sequence)

    def update_latest_step(self, task_id, kind, status, output, error=""):
        matches = [step for step in self.steps if step.task_id == task_id and step.kind == kind]
        if not matches:
            return None
        step = max(matches, key=lambda item: item.sequence)
        step.status = status
        step.output = {**step.output, **output}
        step.error = error
        return step

    def add_event(self, event):
        self.events.append(event)
        return event

    def list_events(self, task_id):
        return [event for event in self.events if event.task_id == task_id]

    def add_artifact(self, artifact):
        self.artifacts.append(artifact)
        return artifact

    def list_artifacts(self, task_id):
        return [artifact for artifact in self.artifacts if artifact.task_id == task_id]

    def list_artifacts_for_tenant(self, tenant_id, kind, limit=1000):
        task_ids = {task.id for task in self.tasks.values() if task.tenant_id == tenant_id}
        values = [artifact for artifact in self.artifacts if artifact.task_id in task_ids and artifact.kind == kind]
        return sorted(values, key=lambda artifact: artifact.created_at)[: max(1, min(limit, 10000))]
