import json
from datetime import datetime, timezone

from app.audit import rotate_audit_log, summarize, write_audit
from app.config import settings


def test_write_audit_normalizes_invalid_numeric_values(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(path, trace_id="trace", sql=None, duration_ms=-1, row_count=-2, input_tokens=-3, output_tokens=-4, cost_usd=float("nan"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["duration_ms"] == 0
    assert record["row_count"] == 0
    assert record["input_tokens"] == 0
    assert record["output_tokens"] == 0
    assert record["cost_usd"] == 0.0
    assert record["usage_source"] == "none"


def test_write_audit_records_valid_usage_source(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(path, trace_id="trace", sql="SELECT 1", duration_ms=1, usage_source="provider")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["usage_source"] == "provider"


def test_write_audit_records_explain_cost_without_sql_or_plan(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(path, trace_id="trace", sql="SELECT amount FROM sales", duration_ms=1, cost_backend="sqlite", cost_supported=True, estimated_rows=42, estimated_cost=3.5, full_scan=True, index_used=False)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["cost_backend"] == "sqlite"
    assert record["estimated_rows"] == 42
    assert record["estimated_cost"] == 3.5
    assert record["full_scan"] is True and record["index_used"] is False
    assert "plan" not in record and "amount" not in record


def test_audit_summarizes_rows_truncation_and_large_results(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(path, trace_id="a", sql="SELECT 1", duration_ms=1, row_count=800, truncated=True, large_result=True)
    write_audit(path, trace_id="b", sql="SELECT 1", duration_ms=1, row_count=2)
    result = summarize(path)
    assert result["total_rows_returned"] == 802
    assert result["truncated_queries"] == 1
    assert result["large_result_queries"] == 1


def test_write_audit_records_trace_task_and_tenant_context(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(path, trace_id="trace", task_id="task-1", tenant_id="tenant-a", sql="SELECT 1", duration_ms=1)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["trace_id"] == "trace"
    assert record["task_id"] == "task-1"
    assert record["tenant_id"] == "tenant-a"
    assert record["schema_version"] == 1
    assert record["log_type"] == "query_pilot_audit"


def test_write_audit_can_mirror_redacted_json_to_stdout(tmp_path, monkeypatch, capsys):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_stdout_enabled", True)

    write_audit(
        path,
        trace_id="trace",
        tenant_id="tenant-a",
        sql="SELECT secret FROM users",
        duration_ms=1,
    )

    mirrored = json.loads(capsys.readouterr().out)
    assert mirrored["log_type"] == "query_pilot_audit"
    assert mirrored["tenant_id"] == "tenant-a"
    assert mirrored["sql_sha256"]
    assert "SELECT secret" not in json.dumps(mirrored)


def test_summarize_counts_invalid_records_without_failing(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"duration_ms": 4, "cost_usd": 0.5}\ninvalid\n[]\n', encoding="utf-8")
    result = summarize(path)
    assert result["requests"] == 1
    assert result["invalid_records"] == 2
    assert result["total_cost_usd"] == 0.5


def test_summarize_streams_and_normalizes_historical_numeric_values(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"duration_ms": "8", "input_tokens": "12", "output_tokens": null, "cost_usd": "bad"}\n',
        encoding="utf-8",
    )
    result = summarize(path)
    assert result["requests"] == 1
    assert result["avg_duration_ms"] == 8
    assert result["total_input_tokens"] == 12
    assert result["total_output_tokens"] == 0
    assert result["total_cost_usd"] == 0
    assert result["invalid_values"] == 2


def test_summarize_includes_configured_rotated_logs(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"duration_ms": 10, "cost_usd": 1}\n', encoding="utf-8")
    (tmp_path / "audit.jsonl.1").write_text(
        '{"duration_ms": 20, "cost_usd": 2}\n', encoding="utf-8"
    )
    (tmp_path / "audit.jsonl.2").write_text("invalid\n", encoding="utf-8")

    result = summarize(path, backup_count=2)

    assert result["requests"] == 2
    assert result["total_cost_usd"] == 3
    assert result["avg_duration_ms"] == 15
    assert result["invalid_records"] == 1


def test_summarize_respects_backup_count_limit(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"duration_ms": 10}\n', encoding="utf-8")
    (tmp_path / "audit.jsonl.1").write_text('{"duration_ms": 20}\n', encoding="utf-8")
    (tmp_path / "audit.jsonl.2").write_text('{"duration_ms": 30}\n', encoding="utf-8")

    assert summarize(path, backup_count=1)["requests"] == 2


def test_summarize_filters_old_rotated_records_by_timestamp(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"timestamp": "2026-09-18T01:00:00Z", "cost_usd": 2}\n',
        encoding="utf-8",
    )
    (tmp_path / "audit.jsonl.1").write_text(
        "\n".join(
            [
                '{"timestamp": "2026-09-17T23:59:59+00:00", "cost_usd": 8}',
                '{"timestamp": "invalid", "cost_usd": 1}',
                '{"cost_usd": 3}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = summarize(
        path,
        backup_count=1,
        since=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )

    assert result["requests"] == 3
    assert result["total_cost_usd"] == 6


def test_audit_log_rotates_and_keeps_backup_limit(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_max_bytes", 300)
    monkeypatch.setattr(settings, "audit_log_backup_count", 2)

    for index in range(8):
        write_audit(path, trace_id=f"trace-{index}", sql="SELECT 1", duration_ms=index)

    assert path.exists()
    assert (tmp_path / "audit.jsonl.1").exists()
    assert (tmp_path / "audit.jsonl.2").exists()
    assert not (tmp_path / "audit.jsonl.3").exists()


def test_rotate_audit_log_does_nothing_when_under_limit(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b"small\n")

    rotate_audit_log(path, 2, max_bytes=100, backup_count=2)

    assert path.read_bytes() == b"small\n"


def test_reflection_audit_records_safe_decision_metadata_only(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(
        path,
        trace_id="trace-reflect",
        task_id="task-1",
        tenant_id="tenant-a",
        sql="SELECT secret_value FROM customers",
        duration_ms=0,
        event="reflection",
        reflection_failure_type="aggregation_grain",
        reflection_action="regenerate_sql",
        reflection_attempt=1,
        reflection_reason_codes=["AGGREGATE_FANOUT_RISK"],
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["reflection_failure_type"] == "aggregation_grain"
    assert record["reflection_action"] == "regenerate_sql"
    assert record["reflection_reason_codes"] == ["AGGREGATE_FANOUT_RISK"]
    assert "secret_value" not in json.dumps(record)
    assert "params" not in json.dumps(record).lower()
