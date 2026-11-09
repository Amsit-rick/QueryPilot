import pytest

from app.domain.task import DataTask, TaskStep
from app.repositories.memory_task_repository import InMemoryTaskRepository
from app.services.task_service import TaskService
from app.services.query_service import QueryExecutionService


def test_data_task_title_and_defaults():
    task = DataTask(question="  查询华东销售额  ", owner_id="7")
    assert task.status == "draft"
    assert task.display_title() == "查询华东销售额"
    assert TaskStep(task_id=task.id, kind="planning").status == "pending"


class FakeTaskRepository:
    def __init__(self):
        self.tasks = {}
        self.steps = []
        self.events = []
        self.artifacts = []

    def create(self, task):
        self.tasks[task.id] = task
        return task

    def get(self, task_id, tenant_id):
        task = self.tasks.get(task_id)
        return task if task and task.tenant_id == tenant_id else None

    def update(self, task):
        self.tasks[task.id] = task
        return task

    def add_step(self, step):
        self.steps.append(step)
        return step

    def list_steps(self, task_id):
        return [step for step in self.steps if step.task_id == task_id]

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

    def list(self, tenant_id, owner_id, limit=20):
        return [t for t in self.tasks.values() if t.tenant_id == tenant_id and t.owner_id == owner_id][:limit]


def test_task_service_controls_lifecycle_and_steps():
    service = TaskService(FakeTaskRepository())
    task = service.create(question=" 查询销售额 ", tenant_id="t1", owner_id="u1", datasource_id="demo")
    assert task.display_title() == "查询销售额"
    service.transition(task.id, "t1", "planning")
    service.transition(task.id, "t1", "running")
    step = service.append_step(task_id=task.id, kind="generate_sql", sequence=0, status="completed")
    assert step.task_id == task.id


def test_task_service_rejects_invalid_transition_and_cross_tenant_access():
    service = TaskService(FakeTaskRepository())
    task = service.create(question="q", tenant_id="t1", owner_id="u1", datasource_id="demo")
    with pytest.raises(ValueError, match="不能从 draft 转换到 completed"):
        service.transition(task.id, "t1", "completed")
    with pytest.raises(LookupError, match="任务不存在"):
        service.transition(task.id, "t2", "planning")


def test_completed_task_can_only_reopen_through_follow_up_service():
    service = TaskService(FakeTaskRepository())
    task = service.create(question="q", tenant_id="t1", owner_id="u1", datasource_id="demo")
    service.transition(task.id, "t1", "planning")
    service.transition(task.id, "t1", "running")
    service.transition(task.id, "t1", "completed")

    with pytest.raises(ValueError, match="不能从 completed 转换到 planning"):
        service.transition(task.id, "t1", "planning")

    reopened = service.begin_follow_up(task.id, "t1")
    assert reopened.status == "planning"
    restored = service.restore_completed_after_follow_up(task.id, "t1", reason="follow_up_failed")
    assert restored.status == "completed"
    assert service.repository.list_events(task.id)[-2].payload["reason"] == "follow_up"
    assert service.repository.list_events(task.id)[-1].payload["reason"] == "follow_up_failed"


def test_task_service_builds_owned_task_detail():
    service = TaskService(InMemoryTaskRepository())
    task = service.create(question="查询销售额", tenant_id="t1", owner_id="u1", datasource_id="demo")
    service.append_step(task_id=task.id, kind="generate_sql", sequence=1, status="completed", duration_ms=12)
    service.append_step(task_id=task.id, kind="understand", sequence=0, status="completed")
    service.append_event(task_id=task.id, kind="task_created", payload={"trace_id": "trace-1"})
    service.add_artifact(task_id=task.id, kind="table", name="查询结果", metadata={"rows": 2})

    detail = service.detail(task_id=task.id, tenant_id="t1", owner_id="u1")

    assert [step.sequence for step in detail["steps"]] == [0, 1]
    assert next(event for event in detail["events"] if event.kind == "task_created").payload["status"] == "draft"
    assert next(event for event in detail["events"] if event.kind == "task_created")
    assert next(event for event in detail["events"] if event.payload.get("trace_id") == "trace-1")
    assert detail["artifacts"][0].metadata["rows"] == 2
    with pytest.raises(LookupError, match="任务不存在"):
        service.detail(task_id=task.id, tenant_id="t1", owner_id="u2")


def test_query_service_keeps_validation_before_execution():
    calls = []
    service = QueryExecutionService(
        validate=lambda sql: calls.append(("validate", sql)) or sql.strip(),
        execute=lambda sql, params: calls.append(("execute", sql, params)) or (sql, params),
    )
    assert service.run(" SELECT 1 ", {"limit": 1}) == ("SELECT 1", {"limit": 1})
    assert calls == [("validate", " SELECT 1 "), ("execute", "SELECT 1", {"limit": 1})]


def test_query_service_applies_injected_policy_after_validation():
    calls = []
    service = QueryExecutionService(
        validate=lambda sql: calls.append("validate") or sql.strip(),
        execute=lambda sql, params: (sql, params),
        apply_policy=lambda sql, identity: calls.append(("policy", identity)) or sql + " LIMIT 10",
    )
    assert service.validate_candidate(" SELECT 1 ", {"tenant_id": "default"}) == "SELECT 1 LIMIT 10"
    assert calls == ["validate", ("policy", {"tenant_id": "default"})]
