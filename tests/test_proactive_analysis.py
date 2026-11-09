from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import require_query_access
from app.config import settings
from app.domain.proactive_analysis import ProactiveAnalysisSpec
from app.main import app
from app.proactive_analysis_runtime import (
    get_proactive_analysis_service,
    reset_memory_proactive_analysis_repository,
)
from app.repositories.memory_proactive_analysis_repository import (
    InMemoryProactiveAnalysisRepository,
)
from app.services.proactive_analysis_runner import ProactiveAnalysisRunner
from app.services.proactive_analysis_service import ProactiveAnalysisService


NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


def _identity(subject="analyst-1", tenant="tenant-a"):
    return {"subject": subject, "tenant_id": tenant, "roles": ["analyst"]}


def _service(*, now=lambda: NOW, audit=None):
    return ProactiveAnalysisService(
        repository=InMemoryProactiveAnalysisRepository(),
        min_interval_minutes=15,
        max_runs_per_day=24,
        max_daily_budget_usd=2.0,
        now=now,
        audit_writer=audit,
    )


def _create(service, **overrides):
    values = {
        "tenant_id": "tenant-a",
        "owner_id": "analyst-1",
        "spec": ProactiveAnalysisSpec(
            question="每天检查区域销售额异常",
            datasource_id="sqlite-demo",
            interval_minutes=60,
            max_runs_per_day=6,
            daily_budget_usd=0.5,
            require_approval=True,
        ),
    }
    values.update(overrides)
    return service.create(**values)


def test_plan_lifecycle_requires_explicit_activation_and_close_is_terminal():
    events = []
    service = _service(audit=lambda **payload: events.append(payload))
    plan = _create(service)

    assert plan.state == "draft"
    assert plan.next_run_at is None
    assert plan.spec.require_approval is True

    active = service.activate(plan.id, tenant_id="tenant-a", owner_id="analyst-1")
    paused = service.pause(plan.id, tenant_id="tenant-a", owner_id="analyst-1")
    resumed = service.resume(plan.id, tenant_id="tenant-a", owner_id="analyst-1")
    closed = service.close(plan.id, tenant_id="tenant-a", owner_id="analyst-1")

    assert active.state == "active" and active.next_run_at == NOW
    assert paused.state == "paused" and paused.next_run_at is None
    assert resumed.state == "active" and resumed.next_run_at == NOW
    assert closed.state == "closed" and closed.next_run_at is None
    with pytest.raises(ValueError, match="E_PROACTIVE_STATE"):
        service.resume(plan.id, tenant_id="tenant-a", owner_id="analyst-1")
    assert [item["event"] for item in events] == [
        "proactive_plan_created",
        "proactive_plan_activated",
        "proactive_plan_paused",
        "proactive_plan_resumed",
        "proactive_plan_closed",
    ]
    assert "question" not in repr(events)


def test_due_plan_executes_once_with_current_identity_and_default_approval():
    service = _service()
    plan = service.activate(_create(service).id, tenant_id="tenant-a", owner_id="analyst-1")
    calls = []

    async def execute(request, identity):
        calls.append((request, identity))
        return {"task_id": "task-1", "trace_id": "trace-1", "status": "approval_required"}

    runner = ProactiveAnalysisRunner(
        service=service,
        query_executor=execute,
        identity_resolver=lambda item: _identity(item.owner_id, item.tenant_id),
        estimate_cost=lambda _question: 0.02,
        now=lambda: NOW,
    )

    report = asyncio.run(runner.run_once(limit=10))
    second = asyncio.run(runner.run_once(limit=10))
    updated = service.get(plan.id, tenant_id="tenant-a", owner_id="analyst-1")
    runs = service.list_runs(plan.id, tenant_id="tenant-a", owner_id="analyst-1")

    assert report.scanned == 1 and report.approval_required == 1
    assert second.scanned == 0
    assert len(calls) == 1
    request, identity = calls[0]
    assert request.question == plan.spec.question
    assert request.datasource == "sqlite-demo"
    assert request.require_approval is True
    assert request.client_request_id.startswith(f"proactive:{plan.id}:")
    assert identity == _identity()
    assert updated.next_run_at == NOW + timedelta(minutes=60)
    assert updated.last_task_id == "task-1"
    assert updated.runs_today == 1
    assert runs[0].status == "approval_required"
    assert runs[0].task_id == "task-1"
    assert "每天检查" not in repr(asdict(runs[0]))


def test_runner_skips_daily_limits_without_calling_query_executor():
    clock = [NOW]
    service = _service(now=lambda: clock[0])
    plan = _create(
        service,
        spec=ProactiveAnalysisSpec(
            question="检查销售异常",
            datasource_id="sqlite-demo",
            interval_minutes=60,
            max_runs_per_day=1,
            daily_budget_usd=0.01,
        ),
    )
    service.activate(plan.id, tenant_id="tenant-a", owner_id="analyst-1")

    async def completed(_request, _identity):
        return {"task_id": "task-first", "trace_id": "trace-first", "row_count": 1}

    first_runner = ProactiveAnalysisRunner(
        service=service,
        query_executor=completed,
        identity_resolver=lambda item: _identity(item.owner_id, item.tenant_id),
        estimate_cost=lambda _question: 0.005,
        now=lambda: clock[0],
    )
    asyncio.run(first_runner.run_once(limit=10))
    clock[0] = NOW + timedelta(minutes=60)

    async def should_not_execute(*_args):
        raise AssertionError("budget limited plans must not execute")

    runner = ProactiveAnalysisRunner(
        service=service,
        query_executor=should_not_execute,
        identity_resolver=lambda item: _identity(item.owner_id, item.tenant_id),
        estimate_cost=lambda _question: 0.02,
        now=lambda: clock[0],
    )

    report = asyncio.run(runner.run_once(limit=10))
    runs = service.list_runs(plan.id, tenant_id="tenant-a", owner_id="analyst-1")

    assert report.skipped_budget == 1
    assert runs[0].status == "skipped_budget"
    assert runs[0].error_code == "E_PROACTIVE_DAILY_RUN_LIMIT"
    assert service.get(plan.id, tenant_id="tenant-a", owner_id="analyst-1").next_run_at > clock[0]


def test_revoked_identity_fails_closed_and_pauses_plan():
    service = _service()
    plan = service.activate(_create(service).id, tenant_id="tenant-a", owner_id="analyst-1")

    runner = ProactiveAnalysisRunner(
        service=service,
        query_executor=lambda *_args: None,
        identity_resolver=lambda _item: (_ for _ in ()).throw(RuntimeError("E_PROACTIVE_IDENTITY_REVOKED")),
        estimate_cost=lambda _question: 0.01,
        now=lambda: NOW,
    )

    report = asyncio.run(runner.run_once(limit=10))
    updated = service.get(plan.id, tenant_id="tenant-a", owner_id="analyst-1")

    assert report.failed == 1
    assert updated.state == "paused"
    assert service.list_runs(plan.id, tenant_id="tenant-a", owner_id="analyst-1")[0].error_code == "E_PROACTIVE_IDENTITY_REVOKED"


def test_api_is_owner_scoped_and_supports_full_lifecycle(monkeypatch):
    reset_memory_proactive_analysis_repository()
    monkeypatch.setattr(settings, "database_backend", "sqlite")
    monkeypatch.setattr(settings, "proactive_analysis_enabled", True)
    app.dependency_overrides[require_query_access] = _identity
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/proactive-analysis/plans",
            json={
                "question": "每小时检查销售异常",
                "datasource_id": "sqlite-demo",
                "interval_minutes": 60,
                "max_runs_per_day": 6,
                "daily_budget_usd": 0.5,
                "require_approval": True,
            },
        )
        plan_id = created.json()["id"]
        activated = client.post(f"/api/v1/proactive-analysis/plans/{plan_id}/activate")
        paused = client.post(f"/api/v1/proactive-analysis/plans/{plan_id}/pause")
        resumed = client.post(f"/api/v1/proactive-analysis/plans/{plan_id}/resume")
        listed = client.get("/api/v1/proactive-analysis/plans")
        runs = client.get(f"/api/v1/proactive-analysis/plans/{plan_id}/runs")

        app.dependency_overrides[require_query_access] = lambda: _identity("analyst-2")
        forbidden = client.get(f"/api/v1/proactive-analysis/plans/{plan_id}/runs")
        app.dependency_overrides[require_query_access] = _identity
        closed = client.post(f"/api/v1/proactive-analysis/plans/{plan_id}/close")
    finally:
        app.dependency_overrides.clear()
        reset_memory_proactive_analysis_repository()

    assert created.status_code == 200 and created.json()["state"] == "draft"
    assert activated.json()["state"] == "active"
    assert paused.json()["state"] == "paused"
    assert resumed.json()["state"] == "active"
    assert listed.status_code == 200 and listed.json()[0]["id"] == plan_id
    assert runs.json() == []
    assert forbidden.status_code == 404
    assert closed.json()["state"] == "closed"


def test_service_rejects_unsafe_frequency_and_budget_bounds():
    service = _service()
    with pytest.raises(ValueError, match="E_PROACTIVE_INTERVAL"):
        _create(
            service,
            spec=ProactiveAnalysisSpec(
                question="频繁检查",
                datasource_id="sqlite-demo",
                interval_minutes=5,
                max_runs_per_day=6,
                daily_budget_usd=0.5,
            ),
        )
    with pytest.raises(ValueError, match="E_PROACTIVE_BUDGET"):
        _create(
            service,
            spec=ProactiveAnalysisSpec(
                question="超预算检查",
                datasource_id="sqlite-demo",
                interval_minutes=60,
                max_runs_per_day=6,
                daily_budget_usd=5,
            ),
        )


def test_api_allows_draft_but_rejects_activation_when_feature_is_disabled(monkeypatch):
    reset_memory_proactive_analysis_repository()
    monkeypatch.setattr(settings, "database_backend", "sqlite")
    monkeypatch.setattr(settings, "proactive_analysis_enabled", False)
    app.dependency_overrides[require_query_access] = _identity
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/proactive-analysis/plans",
            json={
                "question": "每小时检查销售异常",
                "datasource_id": "sqlite-demo",
                "interval_minutes": 60,
                "max_runs_per_day": 6,
                "daily_budget_usd": 0.5,
                "require_approval": True,
            },
        )
        activated = client.post(
            f"/api/v1/proactive-analysis/plans/{created.json()['id']}/activate"
        )
        monkeypatch.setattr(settings, "proactive_analysis_enabled", True)
        active = client.post(
            f"/api/v1/proactive-analysis/plans/{created.json()['id']}/activate"
        )
        paused = client.post(
            f"/api/v1/proactive-analysis/plans/{created.json()['id']}/pause"
        )
        monkeypatch.setattr(settings, "proactive_analysis_enabled", False)
        resumed = client.post(
            f"/api/v1/proactive-analysis/plans/{created.json()['id']}/resume"
        )
    finally:
        app.dependency_overrides.clear()
        reset_memory_proactive_analysis_repository()

    assert created.status_code == 200
    assert created.json()["state"] == "draft"
    assert activated.status_code == 409
    assert activated.json()["detail"].startswith("E_PROACTIVE_DISABLED")
    assert active.status_code == 200 and paused.status_code == 200
    assert resumed.status_code == 409
    assert resumed.json()["detail"].startswith("E_PROACTIVE_DISABLED")


def test_due_claim_is_single_owner_and_expired_lease_can_be_reclaimed():
    repository = InMemoryProactiveAnalysisRepository()
    service = ProactiveAnalysisService(repository=repository, now=lambda: NOW)
    plan = service.activate(_create(service).id, tenant_id="tenant-a", owner_id="analyst-1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_batches = list(
            pool.map(
                lambda _index: repository.claim_due(
                    now=NOW,
                    limit=1,
                    lease_seconds=30,
                ),
                range(2),
            )
        )

    claimed = [item for batch in claimed_batches for item in batch]
    assert len(claimed) == 1
    assert claimed[0].id == plan.id

    assert repository.claim_due(now=NOW + timedelta(seconds=29), limit=1, lease_seconds=30) == []
    reclaimed = repository.claim_due(
        now=NOW + timedelta(seconds=31),
        limit=1,
        lease_seconds=30,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].lease_token != claimed[0].lease_token

    stale = repository.complete_claim(
        plan.id,
        tenant_id="tenant-a",
        lease_token=claimed[0].lease_token,
        run_at=NOW + timedelta(seconds=31),
        next_run_at=NOW + timedelta(minutes=60),
        task_id="stale-task",
        cost_usd=0.01,
        count_run=True,
    )
    assert stale is None


def test_runner_marks_stale_lease_as_failed_instead_of_success():
    service = _service()
    plan = service.activate(_create(service).id, tenant_id="tenant-a", owner_id="analyst-1")
    original_complete = service.repository.complete_claim

    def stale_complete(*args, **kwargs):
        return None

    service.repository.complete_claim = stale_complete

    async def execute(_request, _identity_value):
        return {"task_id": "task-stale", "trace_id": "trace-stale", "status": "completed"}

    runner = ProactiveAnalysisRunner(
        service=service,
        query_executor=execute,
        identity_resolver=lambda item: _identity(item.owner_id, item.tenant_id),
        estimate_cost=lambda _question: 0.01,
        now=lambda: NOW,
    )

    report = asyncio.run(runner.run_once(limit=1))
    service.repository.complete_claim = original_complete
    runs = service.list_runs(plan.id, tenant_id="tenant-a", owner_id="analyst-1")

    assert report.completed == 0
    assert report.failed == 1
    assert report.decisions[0].error_code == "E_PROACTIVE_LEASE_STALE"
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].error_code == "E_PROACTIVE_LEASE_STALE"


def test_cost_estimate_failure_is_isolated_and_does_not_abort_later_plans():
    service = _service()
    broken = _create(
        service,
        spec=ProactiveAnalysisSpec(
            question="成本估算失败",
            datasource_id="sqlite-demo",
            interval_minutes=60,
            max_runs_per_day=6,
            daily_budget_usd=0.5,
        ),
    )
    healthy = _create(
        service,
        spec=ProactiveAnalysisSpec(
            question="正常计划",
            datasource_id="sqlite-demo",
            interval_minutes=60,
            max_runs_per_day=6,
            daily_budget_usd=0.5,
        ),
    )
    service.activate(broken.id, tenant_id="tenant-a", owner_id="analyst-1")
    service.activate(healthy.id, tenant_id="tenant-a", owner_id="analyst-1")
    executed = []

    def estimate(question):
        if question == "成本估算失败":
            raise RuntimeError("provider unavailable")
        return 0.01

    async def execute(request, _identity_value):
        executed.append(request.question)
        return {"task_id": "task-healthy", "trace_id": "trace-healthy"}

    runner = ProactiveAnalysisRunner(
        service=service,
        query_executor=execute,
        identity_resolver=lambda item: _identity(item.owner_id, item.tenant_id),
        estimate_cost=estimate,
        now=lambda: NOW,
    )

    report = asyncio.run(runner.run_once(limit=10))
    broken_runs = service.list_runs(broken.id, tenant_id="tenant-a", owner_id="analyst-1")

    assert report.scanned == 2
    assert report.failed == 1
    assert report.completed == 1
    assert executed == ["正常计划"]
    assert broken_runs[0].error_code == "E_PROACTIVE_COST_ESTIMATE"
    assert service.get(broken.id, tenant_id="tenant-a", owner_id="analyst-1").next_run_at > NOW
