import json

import pytest

from scripts.operations.drill_record import create_record, main, verify_record


def _facts(**overrides):
    payload = {
        "drill_id": "restore-20260918-01",
        "drill_type": "mysql_restore",
        "environment": "isolated-test",
        "started_at": "2026-09-18T08:00:00+08:00",
        "ended_at": "2026-09-18T08:05:00+08:00",
        "outcome": "passed",
        "checks": [{"name": "row count", "status": "passed", "detail": "matched"}],
        "evidence_files": ["restore.log"],
        "rpo_seconds": 60,
        "rto_seconds": 300,
    }
    payload.update(overrides)
    return payload


def test_drill_record_create_and_verify_evidence(tmp_path):
    (tmp_path / "restore.log").write_text("restore completed", encoding="utf-8")

    record = create_record(_facts(), base_dir=tmp_path)

    assert record["format"] == "query-pilot-operations-drill"
    assert verify_record(record, evidence_dir=tmp_path) == []


def test_passed_drill_requires_evidence(tmp_path):
    with pytest.raises(ValueError, match="requires at least one evidence"):
        create_record(_facts(evidence_files=[]), base_dir=tmp_path)


def test_passed_drill_rejects_non_passed_check(tmp_path):
    facts = _facts(checks=[{"name": "restore", "status": "blocked", "detail": "no access"}])

    with pytest.raises(ValueError, match="cannot contain non-passed"):
        create_record(facts, base_dir=tmp_path)


def test_drill_record_detects_tampered_evidence(tmp_path):
    evidence = tmp_path / "restore.log"
    evidence.write_text("restore completed", encoding="utf-8")
    record = create_record(_facts(), base_dir=tmp_path)
    evidence.write_text("changed", encoding="utf-8")

    assert verify_record(record, evidence_dir=tmp_path) == [
        "evidence file integrity mismatch: restore.log"
    ]


def test_passed_drill_requires_evidence_directory_during_verification(tmp_path):
    (tmp_path / "restore.log").write_text("restore completed", encoding="utf-8")
    record = create_record(_facts(), base_dir=tmp_path)

    assert verify_record(record) == ["evidence_dir is required to verify a passed drill"]


def test_blocked_drill_can_record_no_evidence(tmp_path):
    facts = _facts(
        outcome="blocked",
        checks=[{"name": "restore", "status": "blocked", "detail": "DBA approval missing"}],
        evidence_files=[],
    )

    record = create_record(facts, base_dir=tmp_path)

    assert record["outcome"] == "blocked"
    assert verify_record(json.loads(json.dumps(record))) == []


def test_drill_record_cli_refuses_to_overwrite_input(tmp_path):
    source = tmp_path / "facts.json"
    source.write_text(json.dumps(_facts(outcome="blocked", evidence_files=[])), encoding="utf-8")

    code = main(["create", str(source), "--output", str(source)])

    assert code == 2
    assert json.loads(source.read_text(encoding="utf-8"))["drill_id"]


def test_drill_record_rejects_evidence_path_traversal(tmp_path):
    outside = tmp_path.parent / "outside-evidence.log"
    outside.write_text("must not be collected", encoding="utf-8")

    with pytest.raises(ValueError, match="must stay within base_dir"):
        create_record(_facts(evidence_files=["../outside-evidence.log"]), base_dir=tmp_path)


def test_drill_record_rejects_absolute_evidence_outside_base_dir(tmp_path):
    outside = tmp_path.parent / "absolute-evidence.log"
    outside.write_text("must not be collected", encoding="utf-8")

    with pytest.raises(ValueError, match="must stay within base_dir"):
        create_record(_facts(evidence_files=[str(outside)]), base_dir=tmp_path)


def test_drill_record_verification_rejects_evidence_symlink_escape(tmp_path):
    evidence = tmp_path / "restore.log"
    outside = tmp_path.parent / "outside-verify.log"
    outside.write_text("outside", encoding="utf-8")
    try:
        evidence.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    record = {
        **_facts(),
        "evidence": [{"file": "restore.log", "size_bytes": outside.stat().st_size, "sha256": "0" * 64}],
    }
    assert verify_record(record, evidence_dir=tmp_path) == [
        "evidence file escapes evidence_dir: restore.log"
    ]
