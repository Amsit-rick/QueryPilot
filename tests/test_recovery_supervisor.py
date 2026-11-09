from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.agent_recovery import AgentCheckpoint, RecoveryLease, RecoveryLeaseError
from app.domain.query_admission import QueryRequestAdmission
from app.domain.tool_replay import ReplayClass
from app.repositories.memory_query_admission_repository import InMemoryQueryAdmissionRepository
from app.repositories.memory_task_repository import InMemoryTaskRepository
from app.services.query_admission_service import QueryAdmissionService
from app.services.recovery_supervisor_service import RecoverySupervisorService
from app.services.task_recovery_service import TaskRecoveryService
from app.services.task_service import TaskService
from app.services.tool_replay_policy_service import ToolReplayPolicyService


NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


class FakeLeaseService:
    def __init__(self):
        self.checkpoints: dict[tuple[str, str], AgentCheckpoint | Exception | None] = {}
        self.held: set[tuple[str, str]] = set()
        self.released: list[tuple[str, str, int]] = []
        self.fail_acquire_for: set[str] = set()
        self.fail_acquire_generic_for: set[str] = set()
        self.fail_load_for: set[str] = set()
        self._token = 0

    def acquire(self, *, task_id: str, tenant_id: str, holder_id: str) -> RecoveryLease:
        if task_id in self.fail_acquire_generic_for:
            raise RuntimeError("redis unavailable")
        if task_id in self.fail_acquire_for:
            raise RecoveryLeaseError("E_RECOVERY_LEASE_HELD", "held")
        self._token += 1
        self.held.add((tenant_id, task_id))
        return RecoveryLease(
            task_id=task_id,
            tenant_id=tenant_id,
            holder_id=holder_id,
            fencing_token=self._token,
            expires_at=NOW.timestamp() + 60,
        )

    def load(self, *, task_id: str, tenant_id: str) -> AgentCheckpoint | None:
        if task_id in self.fail_load_for:
            raise RuntimeError("backend unavailable")
        value = self.checkpoints.get((tenant_id, task_id))
        if isinstance(value, Exception):
            raise value
        return value

    def release(self, lease: RecoveryLease) -> None:
        self.held.discard((lease.tenant_id, lease.task_id))
        self.released.append((lease.tenant_id, lease.task_id, lease.fencing_token))


def _services():
    task_repository = InMemoryTaskRepository()
    tasks = TaskService(task_repository)
    admission_repository = InMemoryQueryAdmissionRepository()
    admissions = QueryAdmissionService(admission_repository)
    leases = FakeLeaseService()
    supervisor = RecoverySupervisorService(
        tasks=tasks,
        admissions=admissions,
        leases=leases,
        task_recovery=TaskRecoveryService(tasks),
        replay_policies=ToolReplayPolicyService.default(),
        now=lambda: NOW,
    )
    return supervisor, tasks, admissions, admission_repository, leases


def _task(tasks: TaskService, *, tenant: str, status: str = "running", suffix: str = "1"):
    task = tasks.create(
        question=f"query-{suffix}",
        tenant_id=tenant,
        owner_id=f"user-{suffix}",
        datasource_id="sqlite-demo",
    )
    tasks.transition(task.id, tenant, "planning")
    if status != "planning":
        tasks.transition(task.id, tenant, "running")
    if status == "verifying":
        tasks.transition(task.id, tenant, "verifying")
    elif status == "approval_required":
        tasks.transition(task.id, tenant, "approval_required")
    task = tasks.repository.get(task.id, tenant)
    task.updated_at = NOW - timedelta(minutes=10)
    tasks.repository.update(task)
    return task


def _admission(
    repository: InMemoryQueryAdmissionRepository,
    *,
    task,
    status: str,
    error_code: str = "",
):
    admission = QueryRequestAdmission(
        tenant_id=task.tenant_id,
        owner_id=task.owner_id,
        operation="query",
        client_request_id=f"request-{task.id}",
        request_sha256="a" * 64,
        task_id=task.id,
        status=status,
        error_code=error_code,
    )
    repository.claim(admission)
    return admission


def _checkpoint(task, *, tool_name: str, replay_class: ReplayClass, token: int = 1):
    return AgentCheckpoint.create(
        task_id=task.id,
        tenant_id=task.tenant_id,
        execution_id=f"exec-{task.id}",
        node_cursor="validated",
        fencing_token=token,
        snapshot={
            "status": "validated",
            "tool_replay": {
                "version": "tool-replay-v1",
                "tool_name": tool_name,
                "replay_class": replay_class.value,
                "idempotent": replay_class == ReplayClass.SAFE,
            },
        },
    )


def test_scans_only_stale_recoverable_tasks_and_honors_batch_limit():
    supervisor, tasks, *_ = _services()
    first = _task(tasks, tenant="tenant-a", suffix="1")
    second = _task(tasks, tenant="tenant-a", suffix="2")
    fresh = _task(tasks, tenant="tenant-a", suffix="3")
    fresh.updated_at = NOW
    tasks.repository.update(fresh)

    report = supervisor.run_once(limit=1, stale_after_seconds=60, holder_id="worker-a")

    assert report.scanned == 1
    assert len(report.decisions) == 1
    assert report.decisions[0].task_id in {first.id, second.id}
    assert tasks.repository.get(fresh.id, "tenant-a").status == "running"


def test_cross_tenant_candidates_keep_their_own_tenant_scope():
    supervisor, tasks, *_ = _services()
    tenant_a = _task(tasks, tenant="tenant-a", suffix="a")
    tenant_b = _task(tasks, tenant="tenant-b", suffix="b")

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    assert {(item.tenant_id, item.task_id) for item in report.decisions} == {
        ("tenant-a", tenant_a.id),
        ("tenant-b", tenant_b.id),
    }


def test_lease_held_is_skipped_without_mutating_task():
    supervisor, tasks, _admissions, _repository, leases = _services()
    task = _task(tasks, tenant="tenant-a")
    leases.fail_acquire_for.add(task.id)

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-b")

    decision = report.decisions[0]
    assert decision.action == "skip_lease_held"
    assert decision.error_code == "E_RECOVERY_LEASE_HELD"
    assert tasks.repository.get(task.id, "tenant-a").status == "running"
    assert leases.released == []


def test_lease_backend_error_is_isolated_and_does_not_abort_batch():
    supervisor, tasks, _admissions, _repository, leases = _services()
    broken = _task(tasks, tenant="tenant-a", suffix="broken-acquire")
    healthy = _task(tasks, tenant="tenant-a", suffix="healthy-acquire")
    leases.fail_acquire_generic_for.add(broken.id)

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-b")

    by_id = {item.task_id: item for item in report.decisions}
    assert by_id[broken.id].action == "error_isolated"
    assert by_id[broken.id].error_code == "E_RECOVERY_LEASE_BACKEND"
    assert healthy.id in by_id


def test_completed_admission_and_result_asset_reuse_existing_result():
    supervisor, tasks, _admissions, repository, leases = _services()
    task = _task(tasks, tenant="tenant-a", status="verifying")
    _admission(repository, task=task, status="completed")
    tasks.ensure_artifact(
        task_id=task.id,
        kind="table",
        name="result",
        idempotency_key=f"{task.id}:table:query-result",
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    decision = report.decisions[0]
    assert decision.action == "reuse_existing_result"
    assert decision.admission_status == "completed"
    assert decision.fencing_token == 1
    assert tasks.repository.get(task.id, "tenant-a").status == "completed"
    assert leases.released == [("tenant-a", task.id, 1)]


def test_unknown_query_execution_fails_closed_without_reexecution():
    supervisor, tasks, *_rest = _services()
    task = _task(tasks, tenant="tenant-a")
    policy = ToolReplayPolicyService.default().require("query.execute")
    tasks.append_step(
        task_id=task.id,
        kind="query",
        sequence=1,
        status="completed",
        output={"sql": "SELECT secret FROM private", "tool_replay": policy.as_dict()},
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    decision = report.decisions[0]
    assert decision.action == "fail_uncertain_side_effect"
    assert decision.tool_name == "query.execute"
    assert decision.replay_action == "block"
    assert tasks.repository.get(task.id, "tenant-a").status == "failed"
    assert len([step for step in tasks.repository.list_steps(task.id) if step.kind == "query"]) == 1


def test_pending_approval_remains_waiting():
    supervisor, tasks, *_ = _services()
    task = _task(tasks, tenant="tenant-a", status="approval_required")
    tasks.append_step(
        task_id=task.id,
        kind="approval",
        sequence=1,
        status="pending",
        output={"tool_replay": ToolReplayPolicyService.default().require("approval.create").as_dict()},
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    assert report.decisions[0].action == "await_approval"
    assert tasks.repository.get(task.id, "tenant-a").status == "approval_required"


def test_failed_admission_converges_task_to_failed_with_stable_code():
    supervisor, tasks, _admissions, repository, _leases = _services()
    task = _task(tasks, tenant="tenant-a")
    _admission(repository, task=task, status="failed", error_code="E_QUERY_TIMEOUT")

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    decision = report.decisions[0]
    assert decision.action == "fail_from_admission"
    assert decision.error_code == "E_QUERY_TIMEOUT"
    assert tasks.repository.get(task.id, "tenant-a").status == "failed"


def test_safe_checkpoint_creates_resume_plan_but_does_not_claim_execution_completed():
    supervisor, tasks, _admissions, repository, leases = _services()
    task = _task(tasks, tenant="tenant-a")
    _admission(repository, task=task, status="running")
    leases.checkpoints[(task.tenant_id, task.id)] = _checkpoint(
        task,
        tool_name="query.validate",
        replay_class=ReplayClass.SAFE,
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    decision = report.decisions[0]
    assert decision.action == "resume_safe_node"
    assert decision.checkpoint_node == "validated"
    assert decision.replay_action == "replay"
    assert tasks.repository.get(task.id, "tenant-a").status == "running"


def test_compensatable_tool_requires_compensation_and_is_not_replayed():
    supervisor, tasks, _admissions, repository, leases = _services()
    task = _task(tasks, tenant="tenant-a")
    _admission(repository, task=task, status="running")
    leases.checkpoints[(task.tenant_id, task.id)] = _checkpoint(
        task,
        tool_name="export.enqueue",
        replay_class=ReplayClass.COMPENSATABLE,
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    decision = report.decisions[0]
    assert decision.action == "require_compensation"
    assert decision.replay_action == "compensate"
    assert decision.error_code == "E_TOOL_COMPENSATION_REQUIRED"
    assert tasks.repository.get(task.id, "tenant-a").status == "running"


def test_unsupported_checkpoint_version_fails_closed():
    supervisor, tasks, _admissions, _repository, leases = _services()
    task = _task(tasks, tenant="tenant-a")
    leases.checkpoints[(task.tenant_id, task.id)] = RecoveryLeaseError(
        "E_CHECKPOINT_SCHEMA_UNSUPPORTED",
        "unsupported",
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    assert report.decisions[0].action == "fail_invalid_checkpoint"
    assert report.decisions[0].error_code == "E_CHECKPOINT_SCHEMA_UNSUPPORTED"
    assert tasks.repository.get(task.id, "tenant-a").status == "failed"


def test_one_task_failure_does_not_abort_remaining_batch_and_always_releases_lease():
    supervisor, tasks, _admissions, _repository, leases = _services()
    broken = _task(tasks, tenant="tenant-a", suffix="broken")
    healthy = _task(tasks, tenant="tenant-a", suffix="healthy")
    leases.fail_load_for.add(broken.id)

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    by_id = {item.task_id: item for item in report.decisions}
    assert by_id[broken.id].action == "error_isolated"
    assert by_id[broken.id].error_code == "E_RECOVERY_SUPERVISOR_ITEM"
    assert healthy.id in by_id
    assert {item[1] for item in leases.released} == {broken.id, healthy.id}


def test_fencing_takeover_during_recovery_is_skipped_without_aborting_batch(monkeypatch):
    supervisor, tasks, *_ = _services()
    stale = _task(tasks, tenant="tenant-a", suffix="stale")
    healthy = _task(tasks, tenant="tenant-a", suffix="healthy")
    original = tasks.repository.update_recovery_status

    def reject_stale(*, task, fencing_token):
        if task.id == stale.id:
            return None
        return original(task=task, fencing_token=fencing_token)

    monkeypatch.setattr(tasks.repository, "update_recovery_status", reject_stale)

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")

    by_id = {item.task_id: item for item in report.decisions}
    assert by_id[stale.id].action == "skip_stale_fence"
    assert by_id[stale.id].error_code == "E_RECOVERY_FENCING_STALE"
    assert healthy.id in by_id


def test_repeated_scan_does_not_duplicate_supervisor_event():
    supervisor, tasks, *_ = _services()
    task = _task(tasks, tenant="tenant-a", status="approval_required")
    tasks.append_step(task_id=task.id, kind="approval", sequence=1, status="pending")

    first = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")
    second = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-b")

    assert first.decisions[0].action == second.decisions[0].action == "await_approval"
    events = [
        event
        for event in tasks.repository.list_events(task.id)
        if event.kind == "recovery_supervisor"
    ]
    assert len(events) == 1


def test_report_and_event_do_not_contain_sql_parameters_rows_or_checkpoint_payload():
    supervisor, tasks, _admissions, repository, leases = _services()
    task = _task(tasks, tenant="tenant-a")
    _admission(repository, task=task, status="running")
    leases.checkpoints[(task.tenant_id, task.id)] = _checkpoint(
        task,
        tool_name="query.validate",
        replay_class=ReplayClass.SAFE,
    )
    tasks.append_step(
        task_id=task.id,
        kind="validate",
        sequence=1,
        output={"sql": "SELECT secret", "params": {"token": "do-not-store"}},
    )

    report = supervisor.run_once(limit=10, stale_after_seconds=60, holder_id="worker-a")
    serialized_report = repr(asdict(report))
    event = [
        event
        for event in tasks.repository.list_events(task.id)
        if event.kind == "recovery_supervisor"
    ][0]
    serialized_event = repr(event.payload)

    for forbidden in ("SELECT secret", "do-not-store", "request_sha256", "snapshot"):
        assert forbidden not in serialized_report
        assert forbidden not in serialized_event


def test_query_admission_lookup_by_task_is_tenant_scoped():
    repository = InMemoryQueryAdmissionRepository()
    service = QueryAdmissionService(repository)
    admission = QueryRequestAdmission(
        tenant_id="tenant-a",
        owner_id="user-a",
        operation="query",
        client_request_id="request-a",
        request_sha256="a" * 64,
        task_id="task-a",
    )
    repository.claim(admission)

    assert service.find_by_task_id(tenant_id="tenant-a", task_id="task-a") == admission
    assert service.find_by_task_id(tenant_id="tenant-b", task_id="task-a") is None


def test_recovery_status_update_rejects_stale_database_fencing_token():
    tasks = TaskService(InMemoryTaskRepository())
    task = _task(tasks, tenant="tenant-a")

    assert tasks.claim_recovery_fence(
        task_id=task.id,
        tenant_id="tenant-a",
        fencing_token=2,
    )
    with pytest.raises(RecoveryLeaseError, match="E_RECOVERY_FENCING_STALE"):
        tasks.transition_recovered(
            task_id=task.id,
            tenant_id="tenant-a",
            status="failed",
            fencing_token=1,
        )

    assert tasks.repository.get(task.id, "tenant-a").status == "running"


def test_recovery_event_requires_current_database_fencing_token():
    tasks = TaskService(InMemoryTaskRepository())
    task = _task(tasks, tenant="tenant-a")
    assert tasks.claim_recovery_fence(
        task_id=task.id,
        tenant_id="tenant-a",
        fencing_token=2,
    )

    inserted = tasks.append_recovery_event(
        task_id=task.id,
        tenant_id="tenant-a",
        fencing_token=1,
        idempotency_key="recovery-supervisor:stale",
        payload={"action": "await_approval"},
    )

    assert inserted is False
    assert not [event for event in tasks.repository.list_events(task.id) if event.kind == "recovery_supervisor"]


def test_mysql_recoverable_scan_and_admission_lookup_are_bounded_and_tenant_scoped(monkeypatch):
    from app.repositories import mysql_query_admission_repository as admission_module
    from app.repositories import mysql_task_repository as task_module

    calls: list[tuple[str, tuple]] = []

    class Cursor:
        rowcount = 1

        def execute(self, sql, params):
            calls.append((" ".join(sql.split()), tuple(params)))

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(task_module, "connect", lambda: Connection())
    monkeypatch.setattr(admission_module, "connect", lambda: Connection())

    task_module.MySQLTaskRepository().list_recoverable(
        statuses=("running", "verifying"),
        updated_before=NOW,
        limit=25,
    )
    admission_module.MySQLQueryAdmissionRepository().find_by_task_id(
        tenant_id="tenant-a",
        task_id="task-a",
    )
    task_module.MySQLTaskRepository().claim_recovery_fence(
        task_id="task-a",
        tenant_id="tenant-a",
        fencing_token=7,
    )

    scan_sql, scan_params = calls[0]
    admission_sql, admission_params = calls[1]
    assert "status IN (%s,%s)" in scan_sql
    assert "updated_at<=%s" in scan_sql
    assert "ORDER BY updated_at,id LIMIT %s" in scan_sql
    assert scan_params[-1] == 25
    assert "WHERE tenant_id=%s AND task_id=%s" in admission_sql
    assert admission_params == ("tenant-a", "task-a")
    fence_sql, fence_params = calls[2]
    assert "recovery_fencing_token=%s,updated_at=updated_at" in fence_sql
    assert fence_params == (7, "task-a", "tenant-a", 7)

