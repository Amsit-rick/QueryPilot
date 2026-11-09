import json

from app.audit import write_audit


def test_audit_records_event_without_sql_text(tmp_path):
    path = tmp_path / "audit.jsonl"
    write_audit(str(path), trace_id="a1", task_id="task-1", tenant_id="tenant-a", sql="SELECT secret FROM users", duration_ms=0, event="approval_created", actor_id="7", approval_id="p1")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["event"] == "approval_created"
    assert record["actor_id"] == "7"
    assert record["approval_id"] == "p1"
    assert record["task_id"] == "task-1"
    assert record["tenant_id"] == "tenant-a"
    assert "SELECT secret" not in path.read_text(encoding="utf-8")
