from __future__ import annotations

from fastapi.testclient import TestClient

from app.audit import summarize, write_audit
from app.config import settings
from app.main import app
from app.services.agent_trace_service import AgentTraceService, JsonlTraceExporter, summarize_agent_traces


def test_audit_summary_exposes_reflection_recovery_and_rollout_slos(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(
        path,
        trace_id="reflection-ok",
        sql="SELECT 1",
        duration_ms=0,
        event="reflection",
        reflection_action="regenerate_sql",
        reflection_final_status="completed",
        reflection_reason_codes=["SEMANTIC_CRITIC_TIMEOUT"],
    )
    write_audit(
        path,
        trace_id="reflection-failed",
        sql="SELECT 1",
        duration_ms=0,
        event="reflection",
        reflection_action="retry_execution",
        reflection_final_status="failed",
        reflection_reason_codes=["REFLECTION_BUDGET_EXHAUSTED"],
    )
    write_audit(path, trace_id="rollback", sql="", duration_ms=0, event="reflection_rollout_rolled_back")
    write_audit(path, trace_id="recovery", sql="", duration_ms=0, error_code="E_RECOVERY_FENCING_STALE")
    write_audit(path, trace_id="budget", sql="", duration_ms=0, error_code="E_BUDGET_EXHAUSTED")

    result = summarize(path)

    assert result["reflection_decisions"] == 2
    assert result["reflection_repair_successes"] == 1
    assert result["reflection_repair_failures"] == 1
    assert result["reflection_invalid_retries"] == 1
    assert result["reflection_rollbacks"] == 1
    assert result["checkpoint_recovery_failures"] == 1
    assert result["budget_exhausted"] == 2
    assert result["critic_timeouts"] == 1


def test_agent_trace_summary_reports_reflection_latency_without_payloads(tmp_path):
    path = tmp_path / "agent-trace.jsonl"
    service = AgentTraceService(exporter=JsonlTraceExporter(path))
    for duration in (10, 50, 100):
        service.record(
            stage="reflect",
            status="ok",
            duration_ms=duration,
            trace_id=f"trace-{duration}",
            task_id="task-1",
            tenant_id="tenant-a",
        )

    summary = summarize_agent_traces(path)

    assert summary["spans"] == 3
    assert summary["reflection_latency_avg_ms"] == 53.33
    assert summary["reflection_latency_p95_ms"] == 100


def test_metrics_endpoint_exposes_agent_production_metrics(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    trace_path = tmp_path / "trace.jsonl"
    write_audit(audit_path, trace_id="rollback", sql="", duration_ms=0, event="reflection_rollout_rolled_back")
    AgentTraceService(exporter=JsonlTraceExporter(trace_path)).record(
        stage="reflect",
        status="ok",
        duration_ms=42,
        trace_id="trace-1",
        task_id="task-1",
        tenant_id="tenant-a",
    )
    monkeypatch.setattr(settings, "audit_log_path", str(audit_path))
    monkeypatch.setattr(settings, "agent_trace_path", str(trace_path))
    monkeypatch.setattr(settings, "metrics_token", "")

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    body = response.text
    assert "query_pilot_reflection_decisions_total" in body
    assert "query_pilot_reflection_rollbacks_total 1" in body
    assert "query_pilot_checkpoint_recovery_failures_total" in body
    assert 'query_pilot_agent_stage_duration_seconds_count{stage="reflect",status="ok"} 1.0' in body
    assert 'query_pilot_agent_stage_duration_seconds_sum{stage="reflect",status="ok"} 0.042' in body
    assert 'query_pilot_agent_stage_duration_seconds_bucket{le="0.05",stage="reflect",status="ok"} 1.0' in body
