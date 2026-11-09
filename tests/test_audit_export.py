import json

import pytest

from scripts.operations import audit_export
from scripts.operations.audit_export import create_export_manifest, export_audit, main


def _write(path, records):
    path.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")


def test_audit_export_scopes_tenant_task_and_event(tmp_path):
    source = tmp_path / "audit.jsonl"
    output = tmp_path / "exports" / "audit.jsonl"
    _write(
        source,
        [
            {"timestamp": "2026-09-18T01:00:00+00:00", "tenant_id": "a", "task_id": "t1", "event": "query", "sql_sha256": "x"},
            {"timestamp": "2026-09-18T02:00:00+00:00", "tenant_id": "a", "task_id": "t1", "event": "approval_created", "sql_sha256": "y"},
            {"timestamp": "2026-09-18T03:00:00+00:00", "tenant_id": "b", "task_id": "t2", "event": "query", "sql_sha256": "z"},
        ],
    )

    count = export_audit(source, output, tenant_id="a", task_id="t1", event="approval_created")

    assert count == 1
    assert json.loads(output.read_text(encoding="utf-8"))["sql_sha256"] == "y"


def test_audit_export_reads_rotated_logs_and_applies_limit(tmp_path):
    source = tmp_path / "audit.jsonl"
    _write(source, [{"tenant_id": "a", "task_id": "t1", "event": "query", "timestamp": "2026-09-18T01:00:00+00:00"}])
    _write(tmp_path / "audit.jsonl.1", [{"tenant_id": "a", "task_id": "t1", "event": "query", "timestamp": "2026-09-18T00:00:00+00:00"}])
    output = tmp_path / "out.jsonl"

    assert export_audit(source, output, task_id="t1", max_records=1) == 1


def test_audit_export_requires_scope_and_valid_dates(tmp_path):
    source = tmp_path / "audit.jsonl"
    source.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="tenant_id or task_id"):
        export_audit(source, tmp_path / "out.jsonl")
    with pytest.raises(ValueError, match="ISO-8601"):
        export_audit(source, tmp_path / "out.jsonl", tenant_id="a", since="not-a-date")


def test_audit_export_manifest_hashes_file_without_exposing_scope(tmp_path):
    source = tmp_path / "audit.jsonl"
    output = tmp_path / "out.jsonl"
    _write(source, [{"tenant_id": "tenant-secret", "event": "query"}])
    count = export_audit(source, output, tenant_id="tenant-secret")

    manifest = create_export_manifest(
        output,
        record_count=count,
        tenant_id="tenant-secret",
        task_id=None,
        event=None,
        since=None,
        until=None,
    )

    manifest_text = json.dumps(manifest)
    assert manifest["record_count"] == 1
    assert len(manifest["sha256"]) == 64
    assert "tenant-secret" not in manifest_text


def test_audit_export_cli_writes_manifest_for_empty_result(tmp_path):
    source = tmp_path / "audit.jsonl"
    output = tmp_path / "out.jsonl"
    _write(source, [{"tenant_id": "other", "event": "query"}])

    code = main(["--source", str(source), "--output", str(output), "--tenant-id", "wanted"])

    manifest = json.loads((tmp_path / "out.jsonl.manifest.json").read_text(encoding="utf-8"))
    assert code == 0
    assert output.read_text(encoding="utf-8") == ""
    assert manifest["record_count"] == 0


def test_audit_export_normalizes_naive_timestamps_as_utc(tmp_path):
    source = tmp_path / "audit.jsonl"
    output = tmp_path / "out.jsonl"
    _write(source, [{"tenant_id": "a", "timestamp": "2026-09-18T01:00:00"}])

    count = export_audit(source, output, tenant_id="a", since="2026-09-18T00:00:00Z")

    assert count == 1


def test_audit_export_refuses_to_overwrite_source_or_rotated_log(tmp_path):
    source = tmp_path / "audit.jsonl"
    rotated = tmp_path / "audit.jsonl.1"
    _write(source, [{"tenant_id": "a"}])
    _write(rotated, [{"tenant_id": "a"}])

    with pytest.raises(ValueError, match="must not overwrite"):
        export_audit(source, source, tenant_id="a")
    with pytest.raises(ValueError, match="must not overwrite"):
        export_audit(source, rotated, tenant_id="a")


def test_audit_export_keeps_existing_output_when_atomic_replace_fails(tmp_path, monkeypatch):
    source = tmp_path / "audit.jsonl"
    output = tmp_path / "out.jsonl"
    _write(source, [{"tenant_id": "a"}])
    output.write_text("existing\n", encoding="utf-8")

    def fail_replace(source_path, target_path):
        raise OSError("disk")

    monkeypatch.setattr(audit_export.os, "replace", fail_replace)

    with pytest.raises(OSError, match="disk"):
        export_audit(source, output, tenant_id="a")

    assert output.read_text(encoding="utf-8") == "existing\n"


def test_audit_export_rejects_unbounded_backup_scan(tmp_path):
    source = tmp_path / "audit.jsonl"
    _write(source, [{"tenant_id": "a"}])

    with pytest.raises(ValueError, match="backup_count"):
        export_audit(source, tmp_path / "out.jsonl", tenant_id="a", backup_count=101)
