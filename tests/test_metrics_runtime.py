from fastapi.testclient import TestClient

from app.audit import write_audit
from app.main import app
from app.metrics_runtime import record_schema_embedding_request, render_metrics, reset_metrics_for_tests
from app.services.agent_trace_service import AgentTraceService, InMemoryTraceExporter


def test_audit_events_update_native_counters_and_histogram_without_file_scan(tmp_path):
    reset_metrics_for_tests()
    path = tmp_path / "audit.jsonl"
    write_audit(
        path,
        trace_id="trace-1",
        sql="SELECT region FROM sales",
        duration_ms=250,
        row_count=3,
        truncated=True,
        large_result=True,
        error_code="E_QUERY_ERROR",
        input_tokens=11,
        output_tokens=7,
        cost_usd=0.001,
    )
    path.write_text(path.read_text(encoding="utf-8") + "not-json\n", encoding="utf-8")

    body = render_metrics().decode("utf-8")
    assert "query_pilot_requests_total 1.0" in body
    assert "query_pilot_errors_total 1.0" in body
    assert "query_pilot_rows_returned_total 3.0" in body
    assert "query_pilot_truncated_queries_total 1.0" in body
    assert "query_pilot_large_result_queries_total 1.0" in body
    assert "query_pilot_input_tokens_total 11.0" in body
    assert "query_pilot_output_tokens_total 7.0" in body
    assert "query_pilot_request_duration_seconds_count 1.0" in body
    assert "query_pilot_request_duration_seconds_sum 0.25" in body
    assert "query_pilot_audit_invalid_total" not in body


def test_agent_trace_records_native_stage_histogram():
    reset_metrics_for_tests()
    service = AgentTraceService(exporter=InMemoryTraceExporter())
    service.record(
        stage="reflect",
        status="ok",
        duration_ms=42,
        trace_id="trace-1",
        task_id="task-1",
        tenant_id="tenant-1",
    )

    body = render_metrics().decode("utf-8")
    assert 'query_pilot_agent_spans_total{stage="reflect",status="ok"} 1.0' in body
    assert 'query_pilot_agent_stage_duration_seconds_count{stage="reflect",status="ok"} 1.0' in body
    assert 'query_pilot_agent_stage_duration_seconds_sum{stage="reflect",status="ok"} 0.042' in body


def test_metrics_endpoint_does_not_read_existing_jsonl_files(tmp_path, monkeypatch):
    reset_metrics_for_tests()
    audit_path = tmp_path / "audit.jsonl"
    trace_path = tmp_path / "trace.jsonl"
    audit_path.write_text('{"error_code":"E_OLD"}\nnot-json\n', encoding="utf-8")
    trace_path.write_text('{"stage":"reflect","duration_ms":99999}\n', encoding="utf-8")
    monkeypatch.setattr("app.main.settings.audit_log_path", str(audit_path))
    monkeypatch.setattr("app.main.settings.agent_trace_path", str(trace_path))
    monkeypatch.setattr("app.main.settings.metrics_token", "")

    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "query_pilot_requests_total 0.0" in response.text
    assert "99999" not in response.text


def test_schema_embedding_metrics_include_status_latency_and_cache_counts():
    reset_metrics_for_tests()
    record_schema_embedding_request(
        status="ok",
        duration_seconds=0.25,
        documents=3,
        cache_hits=2,
        cache_misses=1,
    )

    body = render_metrics().decode("utf-8")
    assert 'query_pilot_schema_embedding_requests_total{status="ok"} 1.0' in body
    assert 'query_pilot_schema_embedding_duration_seconds_count{status="ok"} 1.0' in body
    assert "query_pilot_schema_embedding_documents_total 3.0" in body
    assert "query_pilot_schema_embedding_cache_hits_total 2.0" in body
    assert "query_pilot_schema_embedding_cache_misses_total 1.0" in body
