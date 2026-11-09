from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.domain.mcp_invocation import McpToolDescriptor
from app.domain.tool_replay import ReplayAction, ReplayClass
from app.main import app
from app.repositories.memory_task_repository import InMemoryTaskRepository
from app.services.task_recovery_service import TaskRecoveryService
from app.services.task_service import TaskService
from app.services.tool_replay_policy_service import ToolReplayPolicyService
from app.task_runtime import get_task_service


def test_default_tool_policies_are_explicit_and_fail_closed():
    policies = ToolReplayPolicyService.default()

    assert policies.require("schema.inspect").replay_class == ReplayClass.SAFE
    assert policies.require("query.execute").replay_class == ReplayClass.UNSAFE
    assert policies.require("approval.create").replay_class == ReplayClass.UNSAFE
    export_policy = policies.require("export.enqueue")
    assert export_policy.replay_class == ReplayClass.COMPENSATABLE
    assert export_policy.compensation_tool == "export.cancel"
    assert policies.resolve("unregistered.tool").replay_class == ReplayClass.UNSAFE
    assert policies.resolve("unregistered.tool").registered is False


def test_recovery_decisions_distinguish_reuse_replay_block_and_compensation():
    policies = ToolReplayPolicyService.default()

    reused = policies.decide("query.execute", invocation_status="completed", result_available=True)
    replayed = policies.decide("schema.inspect", invocation_status="unknown", result_available=False)
    blocked = policies.decide("query.execute", invocation_status="unknown", result_available=False)
    compensate = policies.decide("export.enqueue", invocation_status="unknown", result_available=False)
    replay_after_compensation = policies.decide(
        "export.enqueue",
        invocation_status="failed",
        result_available=False,
        compensation_completed=True,
    )

    assert reused.action == ReplayAction.REUSE_RESULT
    assert replayed.action == ReplayAction.REPLAY
    assert blocked.action == ReplayAction.BLOCK
    assert blocked.error_code == "E_TOOL_REPLAY_UNSAFE"
    assert compensate.action == ReplayAction.COMPENSATE
    assert compensate.compensation_tool == "export.cancel"
    assert replay_after_compensation.action == ReplayAction.REPLAY


def test_mcp_policy_uses_remote_idempotency_annotation():
    policies = ToolReplayPolicyService.default()
    safe = policies.for_mcp_tool(
        McpToolDescriptor(name="schema.list", read_only=True, destructive=False, idempotent=True)
    )
    unsafe = policies.for_mcp_tool(
        McpToolDescriptor(name="report.read", read_only=True, destructive=False, idempotent=False)
    )

    assert safe.tool_name == "mcp.invoke:schema.list"
    assert safe.replay_class == ReplayClass.SAFE
    assert unsafe.replay_class == ReplayClass.UNSAFE


def test_query_task_steps_persist_schema_and_execution_replay_policies():
    client = TestClient(app)
    response = client.post(
        "/api/v1/query",
        json={
            "client_request_id": f"replay-policy-{uuid4()}",
            "question": "查询区域",
            "sql": "SELECT region FROM sales ORDER BY id",
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    detail = get_task_service().detail_for_tenant(task_id=task_id, tenant_id="default")

    understand = next(step for step in detail["steps"] if step.kind == "understand_question")
    query = next(step for step in detail["steps"] if step.kind == "query")

    assert understand.output["tool_replay"]["tool_name"] == "schema.inspect"
    assert understand.output["tool_replay"]["replay_class"] == "safe"
    assert query.output["tool_replay"]["tool_name"] == "query.execute"
    assert query.output["tool_replay"]["replay_class"] == "unsafe"


def test_approval_step_persists_unsafe_replay_policy():
    client = TestClient(app)
    response = client.post(
        "/api/v1/query",
        json={
            "client_request_id": f"approval-policy-{uuid4()}",
            "question": "审批查询",
            "sql": "SELECT region FROM sales",
            "require_approval": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approval_required"
    detail = get_task_service().detail_for_tenant(task_id=body["task_id"], tenant_id="default")
    approval_step = next(step for step in detail["steps"] if step.kind == "approval")

    assert approval_step.output["tool_replay"]["tool_name"] == "approval.create"
    assert approval_step.output["tool_replay"]["replay_class"] == "unsafe"


def test_export_contract_declares_compensation_before_replay(tmp_path, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(main_module.settings, "export_dir", str(tmp_path))
    monkeypatch.setattr(main_module.settings, "export_execution_mode", "inline")
    response = TestClient(app).post(
        "/api/v1/export",
        json={"question": "导出区域", "sql": "SELECT region FROM sales"},
    )

    assert response.status_code == 200
    policy = response.json()["replay_policy"]
    assert policy["tool_name"] == "export.enqueue"
    assert policy["replay_class"] == "compensatable"
    assert policy["compensation_tool"] == "export.cancel"


def test_task_recovery_records_blocked_unsafe_tool_decision():
    repository = InMemoryTaskRepository()
    tasks = TaskService(repository)
    task = tasks.create(
        question="sales",
        tenant_id="tenant-a",
        owner_id="user-a",
        datasource_id="sqlite-demo",
    )
    tasks.transition(task.id, "tenant-a", "planning")
    tasks.transition(task.id, "tenant-a", "running")
    policy = ToolReplayPolicyService.default().require("query.execute")
    tasks.append_step(
        task_id=task.id,
        kind="query",
        sequence=0,
        status="completed",
        output={"sql": "SELECT 1", "tool_replay": policy.as_dict()},
    )

    outcome = TaskRecoveryService(tasks).recover(
        task_id=task.id,
        tenant_id="tenant-a",
        idempotency_key="unsafe-query-recovery",
    )

    assert outcome.action == "failed_uncertain_result"
    assert outcome.replay_action == ReplayAction.BLOCK.value
    assert outcome.blocked_tool == "query.execute"
    event = repository.list_events(task.id)[-1]
    assert event.payload["replay_action"] == "block"
    assert event.payload["blocked_tool"] == "query.execute"
