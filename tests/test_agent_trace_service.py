from __future__ import annotations

import asyncio
import json

import pytest

from app.services.agent_trace_service import (
    AgentTraceService,
    InMemoryTraceExporter,
    JsonlTraceExporter,
    ResilientMultiTraceExporter,
    summarize_agent_traces,
)


def test_trace_wrapper_records_only_safe_span_metadata():
    exporter = InMemoryTraceExporter()
    service = AgentTraceService(exporter=exporter)

    wrapped = service.wrap(
        "execute",
        lambda sql, params: [{"secret": "result-row"}],
        trace_id="trace-1",
        task_id="task-1",
        tenant_id="tenant-a",
    )
    result = wrapped("SELECT * FROM secret_table", {"password": "secret-value"})

    assert result == [{"secret": "result-row"}]
    assert len(exporter.records) == 1
    record = exporter.records[0]
    assert record["stage"] == "execute"
    assert record["status"] == "ok"
    assert record["trace_id"] == "trace-1"
    serialized = json.dumps(record, ensure_ascii=False)
    assert "SELECT" not in serialized
    assert "secret-value" not in serialized
    assert "result-row" not in serialized


def test_async_trace_wrapper_records_sanitized_failure_code():
    exporter = InMemoryTraceExporter()
    service = AgentTraceService(exporter=exporter)

    async def fail(_question):
        error = RuntimeError("database password leaked in internal message")
        error.code = "E_MODEL_TIMEOUT"
        raise error

    wrapped = service.wrap(
        "sql_generate",
        fail,
        trace_id="trace-1",
        task_id="task-1",
        tenant_id="tenant-a",
    )

    with pytest.raises(RuntimeError):
        asyncio.run(wrapped("prompt with private values"))

    assert exporter.records[0]["status"] == "error"
    assert exporter.records[0]["error_code"] == "E_MODEL_TIMEOUT"
    assert "password" not in json.dumps(exporter.records[0])


def test_jsonl_exporter_writes_versioned_trace_record(tmp_path):
    path = tmp_path / "agent-trace.jsonl"
    exporter = JsonlTraceExporter(path)
    service = AgentTraceService(exporter=exporter)

    service.record(
        stage="request",
        status="ok",
        duration_ms=12,
        trace_id="trace-1",
        task_id="task-1",
        tenant_id="tenant-a",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["log_type"] == "query_pilot_agent_trace"
    assert payload["stage"] == "request"
    assert payload["duration_ms"] == 12


def test_trace_stage_is_allowlisted():
    service = AgentTraceService(exporter=InMemoryTraceExporter())

    with pytest.raises(ValueError, match="E_AGENT_TRACE_STAGE"):
        service.record(
            stage="arbitrary_user_controlled_stage",
            status="ok",
            duration_ms=1,
            trace_id="trace-1",
            task_id="task-1",
            tenant_id="tenant-a",
        )


def test_central_export_failure_falls_back_without_breaking_query_trace():
    class BrokenExporter:
        def emit(self, _record):
            raise RuntimeError("collector unavailable")

    fallback = InMemoryTraceExporter()
    exporter = ResilientMultiTraceExporter([BrokenExporter(), fallback])
    service = AgentTraceService(exporter=exporter)

    service.record(
        stage="execute",
        status="ok",
        duration_ms=12,
        trace_id="trace-safe",
        task_id="task-safe",
        tenant_id="tenant-safe",
    )

    assert exporter.export_failures == 1
    assert fallback.records[0]["trace_id"] == "trace-safe"


def test_trace_summary_contains_real_histogram_buckets(tmp_path):
    path = tmp_path / "trace.jsonl"
    service = AgentTraceService(exporter=JsonlTraceExporter(path))
    for duration in (5, 30, 300, 3000):
        service.record(
            stage="reflect",
            status="ok",
            duration_ms=duration,
            trace_id=f"trace-{duration}",
            task_id="task",
            tenant_id="tenant",
        )

    summary = summarize_agent_traces(path)

    assert summary["reflection_latency_count"] == 4
    assert summary["reflection_latency_sum_ms"] == 3335
    assert summary["reflection_latency_buckets"]["10"] == 1
    assert summary["reflection_latency_buckets"]["100"] == 2
    assert summary["reflection_latency_buckets"]["+Inf"] == 4
