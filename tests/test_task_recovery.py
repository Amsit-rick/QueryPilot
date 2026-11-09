from app.repositories.memory_task_repository import InMemoryTaskRepository
from app.services.task_recovery_service import TaskRecoveryService
from app.services.task_service import TaskService


def _task(status="running"):
    repository = InMemoryTaskRepository()
    tasks = TaskService(repository)
    task = tasks.create(question="sales", tenant_id="tenant-a", owner_id="user-a", datasource_id="sqlite-demo")
    tasks.transition(task.id, "tenant-a", "planning")
    tasks.transition(task.id, "tenant-a", "running")
    if status == "verifying": tasks.transition(task.id, "tenant-a", "verifying")
    if status == "approval_required": tasks.transition(task.id, "tenant-a", "approval_required")
    return tasks, task


def test_recovery_completes_only_when_result_asset_already_exists():
    tasks, task = _task("verifying")
    tasks.append_step(task_id=task.id, kind="query", sequence=0, status="completed", output={"sql": "SELECT 1"})
    tasks.ensure_artifact(task_id=task.id, kind="table", name="result", idempotency_key=f"{task.id}:table:query-result")
    recovery = TaskRecoveryService(tasks)

    outcome = recovery.recover(task_id=task.id, tenant_id="tenant-a", idempotency_key="restart-1")

    assert outcome.action == "completed_from_asset"
    assert tasks.repository.get(task.id, "tenant-a").status == "completed"


def test_checkpoint_failure_never_reexecutes_query():
    tasks, task = _task()
    tasks.append_step(task_id=task.id, kind="query", sequence=0, status="completed", output={"sql": "SELECT 1"})
    recovery = TaskRecoveryService(tasks)

    outcome = recovery.recover(task_id=task.id, tenant_id="tenant-a", idempotency_key="checkpoint-1")

    assert outcome.action == "failed_uncertain_result"
    assert tasks.repository.get(task.id, "tenant-a").status == "failed"
    assert len([step for step in tasks.repository.list_steps(task.id) if step.kind == "query"]) == 1


def test_asset_write_failure_does_not_repeat_query_or_asset_callback():
    tasks, task = _task("verifying")
    tasks.append_step(task_id=task.id, kind="query", sequence=0, status="completed", output={"sql": "SELECT 1"})
    calls = []
    recovery = TaskRecoveryService(tasks, execute_query=lambda *_: calls.append("query"), write_asset=lambda *_: calls.append("asset"))

    first = recovery.recover(task_id=task.id, tenant_id="tenant-a", idempotency_key="asset-failure-1")
    second = recovery.recover(task_id=task.id, tenant_id="tenant-a", idempotency_key="asset-failure-1")

    assert first == second
    assert calls == []
    assert len([event for event in tasks.repository.list_events(task.id) if event.kind == "task_recovery"]) == 1


def test_pending_approval_remains_pending_after_restart():
    tasks, task = _task("approval_required")
    recovery = TaskRecoveryService(tasks)

    outcome = recovery.recover(task_id=task.id, tenant_id="tenant-a", idempotency_key="approval-pending", approval_status="pending")

    assert outcome.action == "awaiting_approval"
    assert tasks.repository.get(task.id, "tenant-a").status == "approval_required"


def test_interrupted_processed_approval_fails_when_result_is_uncertain():
    tasks, task = _task("approval_required")
    recovery = TaskRecoveryService(tasks)

    outcome = recovery.recover(task_id=task.id, tenant_id="tenant-a", idempotency_key="approval-approved", approval_status="approved")

    assert outcome.action == "failed_uncertain_approval"
    assert tasks.repository.get(task.id, "tenant-a").status == "failed"


def test_recovery_rejects_cross_tenant_task():
    tasks, task = _task()
    recovery = TaskRecoveryService(tasks)

    try:
        recovery.recover(task_id=task.id, tenant_id="tenant-b", idempotency_key="wrong-tenant")
    except LookupError as exc:
        assert str(exc).startswith("E_TASK_NOT_FOUND")
    else:
        raise AssertionError("cross-tenant recovery must fail")
