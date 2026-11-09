import json

from scripts.production_security_acceptance import build_acceptance


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_local_security_acceptance_passes_but_keeps_external_blockers_explicit(tmp_path):
    evidence = {
        "reflection": _write(tmp_path / "reflection.json", {"format": "query-pilot-reflection-release-gate", "passed": True}),
        "sql_security": _write(tmp_path / "sql.json", {"format": "query-pilot-sql-security-eval", "total": 25, "passed": 25}),
        "schema": _write(tmp_path / "schema.json", {"format": "query-pilot-schema-retrieval-eval", "gate": {"passed": True}}),
        "redis": _write(tmp_path / "redis.json", {"format": "query-pilot-redis-recovery-drill", "passed": True}),
        "embedding_redis": _write(tmp_path / "embedding-redis.json", {
            "format": "query-pilot-schema-embedding-redis-drill",
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
        }),
        "multiprocess_metrics": _write(tmp_path / "multiprocess-metrics.json", {
            "format": "query-pilot-multiprocess-metrics-acceptance",
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
        }),
        "capacity": _write(tmp_path / "capacity.json", {"format": "query-pilot-capacity-report", "gate": {"passed": True}}),
    }

    report = build_acceptance(evidence)

    assert report["local_gate_passed"] is True
    assert report["production_ready"] is False
    assert "REAL_DATABASE_ACCEPTANCE_EXCLUDED" in report["external_blockers"]
    assert "OTLP_COLLECTOR_ACCEPTANCE_NOT_RUN" in report["external_blockers"]


def test_local_security_acceptance_fails_closed_on_missing_or_failed_evidence(tmp_path):
    evidence = {
        "reflection": _write(tmp_path / "reflection.json", {"format": "query-pilot-reflection-release-gate", "passed": False}),
        "sql_security": tmp_path / "missing.json",
    }

    report = build_acceptance(evidence)

    assert report["local_gate_passed"] is False
    assert set(report["failed_local_checks"]) >= {
        "reflection",
        "sql_security",
        "schema",
        "redis",
        "embedding_redis",
        "multiprocess_metrics",
        "capacity",
    }


def test_local_security_acceptance_rejects_incomplete_runtime_check_report(tmp_path):
    evidence = {
        "embedding_redis": _write(tmp_path / "embedding-redis.json", {
            "format": "query-pilot-schema-embedding-redis-drill",
            "external_validation": "executed",
            "passed": True,
            "failed_checks": [],
            "checks": {"redis_started": True},
        }),
    }

    report = build_acceptance(evidence)

    assert report["checks"]["embedding_redis"]["passed"] is False
    assert "embedding_redis" in report["failed_local_checks"]
