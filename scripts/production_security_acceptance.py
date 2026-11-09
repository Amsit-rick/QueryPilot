"""Aggregate local security evidence without claiming external production acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


EXPECTED = {
    "reflection": "query-pilot-reflection-release-gate",
    "sql_security": "query-pilot-sql-security-eval",
    "schema": "query-pilot-schema-retrieval-eval",
    "redis": "query-pilot-redis-recovery-drill",
    "embedding_redis": "query-pilot-schema-embedding-redis-drill",
    "multiprocess_metrics": "query-pilot-multiprocess-metrics-acceptance",
    "capacity": "query-pilot-capacity-report",
}
REQUIRED_REPORT_CHECKS = {
    "embedding_redis": {
        "redis_started",
        "shared_cache_reused",
        "raw_schema_absent",
        "cache_ttl_present",
        "shared_circuit_opened",
        "cache_outage_failed_before_provider",
    },
    "multiprocess_metrics": {
        "server_started",
        "health_reports_multiprocess",
        "queries_succeeded",
        "multiple_worker_metric_files",
        "metrics_aggregated",
        "cleanup_completed",
    },
}


def _passed(name: str, payload: dict) -> bool:
    if name in REQUIRED_REPORT_CHECKS:
        checks = payload.get("checks")
        return (
            payload.get("external_validation") == "executed"
            and payload.get("passed") is True
            and payload.get("failed_checks") == []
            and isinstance(checks, dict)
            and all(checks.get(check) is True for check in REQUIRED_REPORT_CHECKS[name])
        )
    if name in {"reflection", "redis"}:
        return payload.get("passed") is True
    if name == "sql_security":
        total = payload.get("total")
        return isinstance(total, int) and total > 0 and payload.get("passed") == total
    return payload.get("gate", {}).get("passed") is True


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_acceptance(evidence: dict[str, Path]) -> dict:
    checks = {}
    for name, expected_format in EXPECTED.items():
        path = evidence.get(name)
        check = {"passed": False, "file": path.name if path else None, "sha256": None, "reason": "missing"}
        if path and path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                format_ok = isinstance(payload, dict) and payload.get("format") == expected_format
                check.update(
                    passed=format_ok and _passed(name, payload),
                    sha256=_sha256(path),
                    reason="passed" if format_ok and _passed(name, payload) else "invalid_or_failed",
                )
            except (OSError, json.JSONDecodeError):
                check["reason"] = "invalid_json"
        checks[name] = check
    failed = [name for name, check in checks.items() if not check["passed"]]
    external_blockers = [
        "REAL_DATABASE_ACCEPTANCE_EXCLUDED",
        "DATABASE_READONLY_GRANTS_NOT_VERIFIED",
        "NETWORK_IP_ALLOWLIST_NOT_VERIFIED",
        "TLS_AND_SECRET_MANAGER_NOT_VERIFIED",
        "ALERT_NOTIFICATION_CHAIN_NOT_VERIFIED",
        "OTLP_COLLECTOR_ACCEPTANCE_NOT_RUN",
        "REDIS_SENTINEL_OR_CLUSTER_FAILOVER_NOT_RUN",
    ]
    return {
        "format": "query-pilot-production-security-acceptance",
        "version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "local_gate_passed": not failed,
        "failed_local_checks": failed,
        "production_ready": not failed and not external_blockers,
        "checks": checks,
        "external_blockers": external_blockers,
        "scope": {
            "database_acceptance": "excluded_by_request",
            "synthetic_or_local_evidence": True,
        },
        "limitations": [
            "Passing local evidence does not authorize production release.",
            "External infrastructure, DBA grants, networking, TLS, secrets and notification delivery require environment owners to sign off.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in EXPECTED:
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/production-security-acceptance.json"))
    args = parser.parse_args(argv)
    evidence = {name: getattr(args, name) for name in EXPECTED}
    report = build_acceptance(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["local_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
