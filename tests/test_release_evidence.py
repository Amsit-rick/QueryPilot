import json

import pytest

from scripts.operations import release_evidence


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _ci_report(*, blocking=None, not_executed=None):
    names = sorted(release_evidence.REQUIRED_CI_GATES)
    return {
        "gates": [
            {"name": name, "passed": not blocking, "blocking": True, "executed": True}
            for name in names
        ],
        "blocking_failures": blocking or [],
        "not_executed": not_executed or [],
    }


def _sbom():
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": [{"name": "fastapi"}]}


def _alerts(*, threshold_status="needs_enterprise_confirmation", promtool=False, notifications=False):
    return {
        "format": "query-pilot-alert-rules-validation",
        "rule_count": 5,
        "local_validation": "passed",
        "problems": [],
        "threshold_status": threshold_status,
        "official_promtool_executed": promtool,
        "notification_chain_tested": notifications,
    }


def test_release_evidence_accepts_complete_local_evidence(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    note = tmp_path / "migration.txt"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    note.write_text("dry-run passed", encoding="utf-8")
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci, sbom=sbom, additional=[("note", note)], project_root=tmp_path
    )

    assert manifest["local_evidence_complete"] is True
    assert manifest["git_revision"] == "abc123"
    assert all(len(item["sha256"]) == 64 for item in manifest["evidence"])


def test_release_evidence_reports_unexecuted_ci_checks(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    _write_json(ci, _ci_report(not_executed=["npm audit"]))
    _write_json(sbom, _sbom())
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(ci_report=ci, sbom=sbom, project_root=tmp_path)

    assert manifest["local_evidence_complete"] is False
    assert "CI report contains checks that were not executed" in " ".join(manifest["problems"])


def test_release_evidence_rejects_missing_per_gate_execution_flags(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    payload = _ci_report()
    payload["gates"][0].pop("executed")
    _write_json(ci, payload)
    _write_json(sbom, _sbom())
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(ci_report=ci, sbom=sbom, project_root=tmp_path)

    assert manifest["local_evidence_complete"] is False
    assert any("invalid executed flag" in item for item in manifest["problems"])


def test_release_evidence_detects_per_gate_unexecuted_without_summary(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    payload = _ci_report()
    payload["gates"][0]["executed"] = False
    _write_json(ci, payload)
    _write_json(sbom, _sbom())
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(ci_report=ci, sbom=sbom, project_root=tmp_path)

    assert manifest["local_evidence_complete"] is False
    assert "CI report contains checks that were not executed" in " ".join(manifest["problems"])


def test_release_evidence_rejects_partial_static_ci_report(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    payload = _ci_report()
    payload["gates"] = payload["gates"][:2]
    _write_json(ci, payload)
    _write_json(sbom, _sbom())
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(ci_report=ci, sbom=sbom, project_root=tmp_path)

    assert manifest["local_evidence_complete"] is False
    assert manifest["evidence"][0]["summary"]["missing_required_gates"]


def test_release_evidence_rejects_empty_sbom_and_dirty_tree(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, {"bomFormat": "CycloneDX", "components": []})
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", False))

    manifest = release_evidence.build_release_manifest(ci_report=ci, sbom=sbom, project_root=tmp_path)

    assert manifest["local_evidence_complete"] is False
    assert any("SBOM contains no components" in item for item in manifest["problems"])
    assert any("uncommitted changes" in item for item in manifest["problems"])


def test_release_evidence_rejects_duplicate_labels(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: (None, None))

    with pytest.raises(ValueError, match="duplicated"):
        release_evidence.build_release_manifest(
            ci_report=ci, sbom=sbom, additional=[("ci", ci)], project_root=tmp_path
        )


def test_release_evidence_cli_refuses_to_overwrite_input(tmp_path):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())

    code = release_evidence.main(
        ["--ci-report", str(ci), "--sbom", str(sbom), "--output", str(ci)]
    )

    assert code == 2
    assert json.loads(ci.read_text(encoding="utf-8"))["gates"]


def test_release_evidence_separates_valid_reports_from_production_blockers(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    migration = tmp_path / "migration.json"
    licenses = tmp_path / "licenses.json"
    images = tmp_path / "images.json"
    alerts = tmp_path / "alerts.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(
        migration,
        {
            "format": "query-pilot-mysql-migration-plan",
            "planned_count": 1,
            "database_connected": False,
            "database_state_checked": False,
            "migrations": [{"version": "V001", "file": "V001__schema.sql", "size_bytes": 10, "sha256": "a" * 64, "statement_count": 1}],
        },
    )
    _write_json(
        licenses,
        {"format": "query-pilot-license-inventory", "component_count": 1, "unknown_license_count": 0, "inventory_complete": True, "approval_status": "not_reviewed", "components": [{"name": "fastapi"}]},
    )
    _write_json(
        images,
        {"format": "query-pilot-container-image-inventory", "image_count": 1, "digest_pinned_count": 0, "unpinned_count": 1, "all_digest_pinned": False, "scan_status": "not_run", "signature_status": "not_verified", "approval_status": "not_reviewed", "images": [{"reference": "python:3.12-slim", "digest_pinned": False}]},
    )
    _write_json(alerts, _alerts())
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci, sbom=sbom, additional=[("migration", migration), ("licenses", licenses), ("images", images), ("alerts", alerts)], project_root=tmp_path
    )

    assert manifest["local_evidence_complete"] is True
    assert manifest["production_evidence_ready"] is False
    assert len(manifest["production_blockers"]) == 9
    assert {item["kind"] for item in manifest["evidence"]} >= {"migration_plan", "license_inventory", "image_inventory", "alert_rules"}


def test_feedback_regression_evidence_is_valid_but_synthetic_or_not_run_blocks_production(tmp_path, monkeypatch):
    monkeypatch.setattr(release_evidence, "_git_revision", lambda _root: ("abc123", True))
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    feedback = tmp_path / "feedback.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(feedback, {
        "format": "query-pilot-feedback-regression-report",
        "version": 1,
        "dataset_version": "feedback-regression-ci-seed-v1",
        "synthetic": True,
        "external_validation": "not_run",
        "metrics": {"approved_cases": 1},
        "gate": {"passed": False, "reason_codes": ["MODEL_NOT_CONFIGURED"]},
    })

    manifest = release_evidence.build_release_manifest(
        ci_report=ci,
        sbom=sbom,
        additional=[("feedback-regression", feedback)],
        project_root=tmp_path,
    )
    entry = next(item for item in manifest["evidence"] if item["label"] == "feedback-regression")
    assert entry["valid"] is True
    assert entry["kind"] == "feedback_regression"
    assert entry["summary"]["external_validation"] == "not_run"
    assert any("external-model regression was not run" in item for item in manifest["production_blockers"])
    assert any("synthetic CI seed" in item for item in manifest["production_blockers"])


def test_schema_embedding_evidence_is_valid_but_not_run_blocks_production(tmp_path, monkeypatch):
    monkeypatch.setattr(release_evidence, "_git_revision", lambda _root: ("abc123", True))
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    embedding = tmp_path / "embedding.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(embedding, {
        "format": "query-pilot-schema-embedding-acceptance",
        "version": 1,
        "synthetic": True,
        "external_validation": "not_run",
        "gate": {"passed": False, "reason_codes": ["EMBEDDING_NOT_CONFIGURED"]},
        "runtime": {"status": "disabled"},
    })

    manifest = release_evidence.build_release_manifest(
        ci_report=ci,
        sbom=sbom,
        additional=[("schema-embedding", embedding)],
        project_root=tmp_path,
    )

    entry = next(item for item in manifest["evidence"] if item["label"] == "schema-embedding")
    assert entry["valid"] is True
    assert entry["kind"] == "schema_embedding"
    assert entry["summary"]["external_validation"] == "not_run"
    assert any("embedding external-provider acceptance" in item for item in manifest["production_blockers"])


def test_runtime_acceptance_evidence_is_parsed_and_keeps_external_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(release_evidence, "_git_revision", lambda _root: ("abc123", True))
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    embedding_redis = tmp_path / "embedding-redis.json"
    multiprocess_metrics = tmp_path / "multiprocess-metrics.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(embedding_redis, {
        "format": "query-pilot-schema-embedding-redis-drill",
        "version": 1,
        "external_validation": "executed",
        "passed": True,
        "failed_checks": [],
        "checks": {
            "redis_started": True,
            "shared_cache_reused": True,
            "raw_schema_absent": True,
            "cache_ttl_present": True,
            "shared_circuit_opened": True,
            "cache_outage_failed_before_provider": True,
        },
        "duration_ms": 10.0,
    })
    _write_json(multiprocess_metrics, {
        "format": "query-pilot-multiprocess-metrics-acceptance",
        "version": 1,
        "external_validation": "executed",
        "passed": True,
        "failed_checks": [],
        "checks": {
            "server_started": True,
            "health_reports_multiprocess": True,
            "queries_succeeded": True,
            "multiple_worker_metric_files": True,
            "metrics_aggregated": True,
            "cleanup_completed": True,
        },
        "duration_ms": 20.0,
    })

    manifest = release_evidence.build_release_manifest(
        ci_report=ci,
        sbom=sbom,
        additional=[
            ("schema-embedding-redis", embedding_redis),
            ("multiprocess-metrics", multiprocess_metrics),
        ],
        project_root=tmp_path,
    )

    entries = {item["label"]: item for item in manifest["evidence"]}
    assert entries["schema-embedding-redis"]["kind"] == "schema_embedding_redis"
    assert entries["multiprocess-metrics"]["kind"] == "multiprocess_metrics"
    assert entries["schema-embedding-redis"]["valid"] is True
    assert entries["multiprocess-metrics"]["valid"] is True
    assert any("synthetic embedding transport" in item for item in manifest["production_blockers"])
    assert any("Alertmanager delivery" in item for item in manifest["production_blockers"])


def test_release_evidence_rejects_inconsistent_specialized_report(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    images = tmp_path / "images.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(
        images,
        {"format": "query-pilot-container-image-inventory", "image_count": 2, "digest_pinned_count": 1, "unpinned_count": 1, "all_digest_pinned": False, "images": [{"reference": "python:3.12-slim", "digest_pinned": False}]},
    )
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci, sbom=sbom, additional=[("images", images)], project_root=tmp_path
    )

    assert manifest["local_evidence_complete"] is False
    assert any("summary does not match" in problem for problem in manifest["problems"])


def test_release_evidence_strict_mode_fails_for_valid_but_unapproved_inventory(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    licenses = tmp_path / "licenses.json"
    output = tmp_path / "release.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(
        licenses,
        {"format": "query-pilot-license-inventory", "component_count": 1, "unknown_license_count": 0, "inventory_complete": True, "approval_status": "not_reviewed", "components": [{"name": "fastapi"}]},
    )
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    code = release_evidence.main(
        ["--ci-report", str(ci), "--sbom", str(sbom), "--evidence", f"licenses={licenses}", "--output", str(output), "--require-production-evidence"]
    )

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["local_evidence_complete"] is True


def test_release_evidence_rejects_claimed_pin_without_digest(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    images = tmp_path / "images.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(
        images,
        {
            "format": "query-pilot-container-image-inventory",
            "image_count": 1,
            "digest_pinned_count": 1,
            "unpinned_count": 0,
            "all_digest_pinned": True,
            "images": [{"reference": "python@sha256:bad", "digest_pinned": True}],
        },
    )
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci, sbom=sbom, additional=[("images", images)], project_root=tmp_path
    )

    assert manifest["local_evidence_complete"] is False
    assert any("invalid pinned digest" in problem for problem in manifest["problems"])


def test_release_evidence_marks_complete_approved_evidence_ready(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    migration = tmp_path / "migration.json"
    licenses = tmp_path / "licenses.json"
    images = tmp_path / "images.json"
    alerts = tmp_path / "alerts.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(
        migration,
        {
            "format": "query-pilot-mysql-migration-plan",
            "planned_count": 0,
            "database_connected": True,
            "database_state_checked": True,
            "migrations": [],
        },
    )
    _write_json(
        licenses,
        {
            "format": "query-pilot-license-inventory",
            "component_count": 1,
            "unknown_license_count": 0,
            "inventory_complete": True,
            "approval_status": "approved",
            "components": [{"name": "fastapi"}],
        },
    )
    _write_json(
        images,
        {
            "format": "query-pilot-container-image-inventory",
            "image_count": 1,
            "digest_pinned_count": 1,
            "unpinned_count": 0,
            "all_digest_pinned": True,
            "scan_status": "passed",
            "signature_status": "verified",
            "approval_status": "approved",
            "images": [
                {
                    "reference": f"python@sha256:{'a' * 64}",
                    "digest": f"sha256:{'a' * 64}",
                    "digest_pinned": True,
                }
            ],
        },
    )
    _write_json(alerts, _alerts(threshold_status="confirmed", promtool=True, notifications=True))
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci,
        sbom=sbom,
        additional=[("migration", migration), ("licenses", licenses), ("images", images), ("alerts", alerts)],
        project_root=tmp_path,
    )

    assert manifest["local_evidence_complete"] is True
    assert manifest["production_evidence_ready"] is True
    assert manifest["production_blockers"] == []


def test_release_evidence_rejects_inconsistent_alert_report(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    alerts = tmp_path / "alerts.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    payload = _alerts()
    payload["problems"] = ["bad rule"]
    _write_json(alerts, payload)
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci, sbom=sbom, additional=[("alerts", alerts)], project_root=tmp_path
    )

    assert manifest["local_evidence_complete"] is False
    assert any("marked passed but contains problems" in item for item in manifest["problems"])


def test_release_evidence_rejects_boolean_summary_counts(tmp_path, monkeypatch):
    ci = tmp_path / "ci.json"
    sbom = tmp_path / "sbom.json"
    images = tmp_path / "images.json"
    _write_json(ci, _ci_report())
    _write_json(sbom, _sbom())
    _write_json(
        images,
        {
            "format": "query-pilot-container-image-inventory",
            "image_count": True,
            "digest_pinned_count": 0,
            "unpinned_count": 1,
            "all_digest_pinned": False,
            "images": [{"reference": "python:3.12-slim", "digest_pinned": False}],
        },
    )
    monkeypatch.setattr(release_evidence, "_git_revision", lambda root: ("abc123", True))

    manifest = release_evidence.build_release_manifest(
        ci_report=ci, sbom=sbom, additional=[("images", images)], project_root=tmp_path
    )

    assert manifest["local_evidence_complete"] is False
    assert any("counts are invalid" in problem for problem in manifest["problems"])
