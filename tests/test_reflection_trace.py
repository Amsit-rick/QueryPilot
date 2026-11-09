from app.agent.reflection import ReflectionAction, ReflectionFailureType, ReflectionResult
from app.repositories.memory_task_repository import InMemoryTaskRepository
from app.services.reflection_trace_service import ReflectionTraceService
from app.services.task_service import TaskService


def test_reflection_trace_persists_safe_step_and_event_only():
    tasks = TaskService(InMemoryTaskRepository())
    task = tasks.create(question="查询", tenant_id="tenant-a", owner_id="viewer-1", datasource_id="demo")
    reflection = ReflectionResult(
        accepted=False,
        failure_type=ReflectionFailureType.AGGREGATION_GRAIN,
        reason_codes=["AGGREGATE_FANOUT_RISK"],
        evidence=["aggregate_from=customers;many_side=orders"],
        action=ReflectionAction.REGENERATE_SQL,
        repair_instruction="先聚合明细表后再关联。",
        retryable=True,
        confidence=1.0,
    )

    next_sequence = ReflectionTraceService(tasks).persist(
        task_id=task.id,
        start_sequence=2,
        reflections=[reflection.model_dump(mode="json")],
        sql="SELECT secret FROM customers",
        model="deepseek-chat",
        prompt_version="query-v7",
        final_status="completed",
    )

    detail = tasks.detail(task_id=task.id, tenant_id="tenant-a", owner_id="viewer-1")
    step = detail["steps"][0]
    event = detail["events"][-1]
    assert next_sequence == 3
    assert step.kind == "reflection"
    assert step.output["failure_type"] == "aggregation_grain"
    assert step.output["action"] == "regenerate_sql"
    assert step.output["attempt"] == 1
    assert len(step.output["sql_sha256"]) == 64
    assert event.kind == "reflection_recorded"
    serialized = str({"step": step.output, "event": event.payload})
    assert "SELECT secret" not in serialized
    assert "params" not in serialized.lower()


def test_reflection_trace_viewer_projection_removes_internal_evidence():
    payload = {
        "failure_type": "aggregation_grain",
        "reason_codes": ["AGGREGATE_FANOUT_RISK"],
        "action": "regenerate_sql",
        "attempt": 1,
        "evidence": ["aggregate_from=customers;many_side=orders"],
        "sql_sha256": "a" * 64,
        "model": "deepseek-chat",
        "prompt_version": "query-v7",
    }

    public = ReflectionTraceService.viewer_projection(payload)

    assert public == {
        "failure_type": "aggregation_grain",
        "reason_codes": ["AGGREGATE_FANOUT_RISK"],
        "action": "regenerate_sql",
        "attempt": 1,
        "summary": "检测到聚合粒度风险，已重新生成查询。",
    }

