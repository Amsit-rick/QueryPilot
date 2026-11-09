"""Build a tamper-evident release evidence index without claiming deployment success."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "query-pilot-release-evidence"
VERSION = 2
CHUNK_SIZE = 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_CI_GATES = {
    "迁移静态检查",
    "密钥扫描",
    "供应链与容器静态检查",
    "依赖漏洞扫描",
    "后端测试",
    "高风险回归",
    "SBOM 生成",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {path.name}") from exc


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _inspect_ci_report(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict):
        return False, {}, ["CI report must be a JSON object"], []
    results = payload.get("gates", payload.get("results"))
    if not isinstance(results, list):
        return False, {}, ["CI report does not contain a gates/results list"], []
    blockers = payload.get("blocking_failures", [])
    not_executed = payload.get("not_executed", [])
    if not isinstance(blockers, list) or not isinstance(not_executed, list):
        return False, {}, ["CI report summary fields are invalid"], []
    problems = []
    computed_blockers = []
    computed_not_executed = []
    gate_names = set()
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            problems.append(f"CI gate {index} is not an object")
            continue
        name = result.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"CI gate {index} has no valid name")
            continue
        gate_names.add(name)
        for field in ("passed", "blocking", "executed"):
            if not isinstance(result.get(field), bool):
                problems.append(f"CI gate {name} has invalid {field} flag")
        if result.get("executed") is False:
            computed_not_executed.append(name)
        if result.get("blocking") is True and result.get("passed") is False:
            computed_blockers.append(name)
    missing_gates = sorted(REQUIRED_CI_GATES - gate_names)
    if blockers or computed_blockers:
        problems.append("CI report contains blocking failures")
    if not_executed or computed_not_executed:
        problems.append("CI report contains checks that were not executed")
    if missing_gates:
        problems.append("CI report is missing required gates")
    return not problems, {
        "gate_count": len(results),
        "blocking_failures": sorted(set(str(item) for item in [*blockers, *computed_blockers])),
        "not_executed": sorted(set(str(item) for item in [*not_executed, *computed_not_executed])),
        "missing_required_gates": missing_gates,
    }, problems, []


def _inspect_sbom(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("bomFormat") != "CycloneDX":
        return False, {}, ["SBOM is not a CycloneDX document"], []
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        return False, {}, ["SBOM contains no components"], []
    return True, {
        "spec_version": payload.get("specVersion"),
        "component_count": len(components),
    }, [], []


def _inspect_migration_plan(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-mysql-migration-plan":
        return False, {}, ["migration evidence is not a Query Pilot migration plan"], []
    migrations = payload.get("migrations")
    planned_count = payload.get("planned_count")
    if not isinstance(migrations, list) or not _is_nonnegative_int(planned_count):
        return False, {}, ["migration plan counts are invalid"], []
    problems = []
    if planned_count != len(migrations):
        problems.append("migration planned_count does not match migrations")
    for index, migration in enumerate(migrations):
        if not isinstance(migration, dict):
            problems.append(f"migration entry {index} is not an object")
            continue
        if not isinstance(migration.get("version"), str) or not isinstance(migration.get("file"), str):
            problems.append(f"migration entry {index} has invalid identity fields")
        if not _is_nonnegative_int(migration.get("size_bytes")):
            problems.append(f"migration entry {index} has invalid size")
        if not _is_nonnegative_int(migration.get("statement_count")):
            problems.append(f"migration entry {index} has invalid statement count")
        digest = migration.get("sha256")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest.lower()):
            problems.append(f"migration entry {index} has invalid sha256")
    production_blockers = []
    if payload.get("database_connected") is not True or payload.get("database_state_checked") is not True:
        production_blockers.append("migration plan was not checked against the target database")
    return not problems, {
        "planned_count": planned_count,
        "database_connected": payload.get("database_connected") is True,
        "database_state_checked": payload.get("database_state_checked") is True,
    }, problems, production_blockers


def _inspect_license_inventory(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-license-inventory":
        return False, {}, ["license evidence is not a Query Pilot license inventory"], []
    component_count = payload.get("component_count")
    unknown_count = payload.get("unknown_license_count")
    components = payload.get("components")
    if not _is_nonnegative_int(component_count) or not _is_nonnegative_int(unknown_count) or not isinstance(components, list):
        return False, {}, ["license inventory counts are invalid"], []
    problems = []
    if component_count != len(components):
        problems.append("license component_count does not match components")
    if unknown_count > component_count:
        problems.append("license unknown count is outside the component range")
    expected_complete = unknown_count == 0
    if payload.get("inventory_complete") is not expected_complete:
        problems.append("license inventory_complete is inconsistent with unknown count")
    production_blockers = []
    if unknown_count:
        production_blockers.append(f"license metadata is missing for {unknown_count} components")
    approval_status = payload.get("approval_status")
    if approval_status != "approved":
        production_blockers.append("license policy approval is not complete")
    return not problems, {
        "component_count": component_count,
        "unknown_license_count": unknown_count,
        "inventory_complete": expected_complete,
        "approval_status": approval_status,
    }, problems, production_blockers


def _inspect_image_inventory(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-container-image-inventory":
        return False, {}, ["image evidence is not a Query Pilot image inventory"], []
    images = payload.get("images")
    image_count = payload.get("image_count")
    pinned_count = payload.get("digest_pinned_count")
    unpinned_count = payload.get("unpinned_count")
    if not isinstance(images, list) or not images or not all(
        _is_nonnegative_int(value) for value in (image_count, pinned_count, unpinned_count)
    ):
        return False, {}, ["image inventory counts are invalid"], []
    problems = []
    computed_pinned = 0
    for index, image in enumerate(images):
        if not isinstance(image, dict) or not isinstance(image.get("reference"), str):
            problems.append(f"image entry {index} is invalid")
            continue
        if not isinstance(image.get("digest_pinned"), bool):
            problems.append(f"image entry {index} has invalid digest_pinned")
            continue
        computed_pinned += int(image["digest_pinned"])
        if image["digest_pinned"]:
            digest = image.get("digest")
            if not isinstance(digest, str) or not IMAGE_DIGEST_PATTERN.fullmatch(digest.lower()):
                problems.append(f"image entry {index} has invalid pinned digest")
    computed_unpinned = len(images) - computed_pinned
    if image_count != len(images) or pinned_count != computed_pinned or unpinned_count != computed_unpinned:
        problems.append("image inventory summary does not match image entries")
    if payload.get("all_digest_pinned") is not (computed_unpinned == 0):
        problems.append("image all_digest_pinned is inconsistent with image entries")
    production_blockers = []
    if computed_unpinned:
        production_blockers.append(f"{computed_unpinned} container image references are not digest pinned")
    if payload.get("scan_status") != "passed":
        production_blockers.append("container image vulnerability scan has not passed")
    if payload.get("signature_status") != "verified":
        production_blockers.append("container image signatures have not been verified")
    if payload.get("approval_status") != "approved":
        production_blockers.append("container image release approval is not complete")
    return not problems, {
        "image_count": image_count,
        "digest_pinned_count": computed_pinned,
        "unpinned_count": computed_unpinned,
        "scan_status": payload.get("scan_status"),
        "signature_status": payload.get("signature_status"),
        "approval_status": payload.get("approval_status"),
    }, problems, production_blockers


def _inspect_alert_report(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-alert-rules-validation":
        return False, {}, ["alert evidence is not a Query Pilot alert validation report"], []
    rule_count = payload.get("rule_count")
    local_validation = payload.get("local_validation")
    problems_value = payload.get("problems")
    threshold_status = payload.get("threshold_status")
    promtool_executed = payload.get("official_promtool_executed")
    notification_tested = payload.get("notification_chain_tested")
    if (
        not _is_nonnegative_int(rule_count)
        or local_validation not in {"passed", "failed"}
        or not isinstance(problems_value, list)
        or threshold_status not in {"needs_enterprise_confirmation", "confirmed"}
        or not isinstance(promtool_executed, bool)
        or not isinstance(notification_tested, bool)
    ):
        return False, {}, ["alert validation report fields are invalid"], []
    problems = []
    if local_validation == "passed" and problems_value:
        problems.append("alert report is marked passed but contains problems")
    if local_validation == "failed" and not problems_value:
        problems.append("alert report is marked failed without problems")
    if rule_count == 0:
        problems.append("alert report contains no rules")
    production_blockers = []
    if threshold_status != "confirmed":
        production_blockers.append("alert thresholds are not enterprise-confirmed")
    if not promtool_executed:
        production_blockers.append("official promtool validation has not been executed")
    if not notification_tested:
        production_blockers.append("alert notification chain has not been tested")
    return not problems, {
        "rule_count": rule_count,
        "local_validation": local_validation,
        "threshold_status": threshold_status,
        "official_promtool_executed": promtool_executed,
        "notification_chain_tested": notification_tested,
    }, problems, production_blockers


def _inspect_feedback_regression(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-feedback-regression-report":
        return False, {}, ["feedback regression evidence has an invalid format"], []
    metrics = payload.get("metrics")
    gate = payload.get("gate")
    status = payload.get("external_validation")
    if not isinstance(metrics, dict) or not isinstance(gate, dict) or status not in {"passed", "failed", "not_run"}:
        return False, {}, ["feedback regression evidence has an invalid contract"], []
    approved_cases = metrics.get("approved_cases")
    if not _is_nonnegative_int(approved_cases) or approved_cases == 0 or not isinstance(gate.get("passed"), bool):
        return False, {}, ["feedback regression evidence has invalid case or gate fields"], []
    problems = []
    if status == "failed" or (status == "passed" and gate.get("passed") is not True):
        problems.append("feedback regression gate failed")
    production_blockers = []
    if status == "not_run":
        production_blockers.append("approved feedback external-model regression was not run")
    if payload.get("synthetic") is True:
        production_blockers.append("feedback regression used a synthetic CI seed instead of an exported approved dataset")
    return not problems, {
        "dataset_version": payload.get("dataset_version"),
        "approved_cases": approved_cases,
        "external_validation": status,
        "gate_passed": gate.get("passed") is True,
        "synthetic": payload.get("synthetic") is True,
    }, problems, production_blockers


def _inspect_business_gold(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-business-gold-report":
        return False, {}, ["business gold evidence has an invalid format"], []
    metrics = payload.get("metrics")
    local_gate = payload.get("local_gate")
    external_gate = payload.get("external_gate")
    status = payload.get("external_validation")
    if not isinstance(metrics, dict) or not isinstance(local_gate, dict) or not isinstance(external_gate, dict):
        return False, {}, ["business gold evidence has an invalid contract"], []
    if status not in {"passed", "failed", "not_run"} or local_gate.get("passed") is not True:
        return False, {}, ["business gold local integrity gate failed"], []
    problems = []
    if status == "failed" or (status == "passed" and external_gate.get("passed") is not True):
        problems.append("business gold current-model gate failed")
    production_blockers = []
    if status == "not_run":
        production_blockers.append("business gold current-model evaluation was not run")
    if payload.get("synthetic") is True:
        production_blockers.append("business gold uses synthetic data instead of enterprise-confirmed questions and results")
    return not problems, {
        "dataset_version": payload.get("dataset_version"),
        "cases": metrics.get("cases"),
        "gold_integrity_rate": metrics.get("gold_integrity_rate"),
        "result_exact_match_rate": metrics.get("result_exact_match_rate"),
        "external_validation": status,
        "synthetic": payload.get("synthetic") is True,
    }, problems, production_blockers


def _inspect_schema_embedding(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != "query-pilot-schema-embedding-acceptance":
        return False, {}, ["schema embedding evidence has an invalid format"], []
    status = payload.get("external_validation")
    gate = payload.get("gate")
    if status not in {"passed", "failed", "not_run"} or not isinstance(gate, dict) or not isinstance(gate.get("passed"), bool):
        return False, {}, ["schema embedding evidence has an invalid contract"], []
    problems = []
    if status == "failed" or (status == "passed" and gate.get("passed") is not True):
        problems.append("schema embedding acceptance failed")
    production_blockers = []
    if status == "not_run":
        production_blockers.append("schema embedding external-provider acceptance was not run")
    return not problems, {
        "external_validation": status,
        "gate_passed": gate.get("passed") is True,
        "synthetic": payload.get("synthetic") is True,
        "document_count": payload.get("document_count", 0),
        "repeat_consistent": payload.get("repeat_consistent", False),
    }, problems, production_blockers


def _inspect_executed_check_report(
    payload: object,
    *,
    expected_format: str,
    report_name: str,
    required_checks: set[str],
    production_blockers: list[str],
) -> tuple[bool, dict, list[str], list[str]]:
    if not isinstance(payload, dict) or payload.get("format") != expected_format:
        return False, {}, [f"{report_name} evidence has an invalid format"], []
    status = payload.get("external_validation")
    passed = payload.get("passed")
    checks = payload.get("checks")
    failed_checks = payload.get("failed_checks")
    if (
        status != "executed"
        or not isinstance(passed, bool)
        or not isinstance(checks, dict)
        or not checks
        or not all(isinstance(name, str) and isinstance(value, bool) for name, value in checks.items())
        or not isinstance(failed_checks, list)
        or not all(isinstance(item, str) for item in failed_checks)
    ):
        return False, {}, [f"{report_name} evidence has an invalid contract"], []
    computed_failed = sorted(name for name, value in checks.items() if value is not True)
    problems = []
    missing_checks = sorted(required_checks - set(checks))
    if missing_checks:
        problems.append(f"{report_name} is missing required checks")
    if sorted(failed_checks) != computed_failed:
        problems.append(f"{report_name} failed-check summary is inconsistent")
    if passed != (not computed_failed):
        problems.append(f"{report_name} pass flag is inconsistent")
    if not passed:
        problems.append(f"{report_name} acceptance failed")
    return not problems, {
        "external_validation": status,
        "passed": passed,
        "check_count": len(checks),
        "duration_ms": payload.get("duration_ms"),
    }, problems, production_blockers


def _inspect_schema_embedding_redis(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    return _inspect_executed_check_report(
        payload,
        expected_format="query-pilot-schema-embedding-redis-drill",
        report_name="schema embedding Redis drill",
        required_checks={
            "redis_started",
            "shared_cache_reused",
            "raw_schema_absent",
            "cache_ttl_present",
            "shared_circuit_opened",
            "cache_outage_failed_before_provider",
        },
        production_blockers=[
            "schema embedding Redis drill used deterministic synthetic embedding transport",
            "schema embedding Redis drill did not exercise Sentinel or Cluster failover",
        ],
    )


def _inspect_multiprocess_metrics(payload: object) -> tuple[bool, dict, list[str], list[str]]:
    return _inspect_executed_check_report(
        payload,
        expected_format="query-pilot-multiprocess-metrics-acceptance",
        report_name="multiprocess metrics acceptance",
        required_checks={
            "server_started",
            "health_reports_multiprocess",
            "queries_succeeded",
            "multiple_worker_metric_files",
            "metrics_aggregated",
            "cleanup_completed",
        },
        production_blockers=[
            "multiprocess metrics acceptance did not exercise Prometheus scraping or Alertmanager delivery",
        ],
    )


SPECIAL_EVIDENCE_INSPECTORS = {
    "migration": ("migration_plan", _inspect_migration_plan),
    "licenses": ("license_inventory", _inspect_license_inventory),
    "images": ("image_inventory", _inspect_image_inventory),
    "alerts": ("alert_rules", _inspect_alert_report),
    "feedback-regression": ("feedback_regression", _inspect_feedback_regression),
    "business-gold": ("business_gold", _inspect_business_gold),
    "schema-embedding": ("schema_embedding", _inspect_schema_embedding),
    "schema-embedding-redis": ("schema_embedding_redis", _inspect_schema_embedding_redis),
    "multiprocess-metrics": ("multiprocess_metrics", _inspect_multiprocess_metrics),
}
REQUIRED_PRODUCTION_EVIDENCE = {"migration", "licenses", "images", "alerts"}


def _evidence_entry(label: str, path: Path, kind: str) -> dict:
    entry = {
        "label": label,
        "kind": kind,
        "file": path.name,
        "exists": path.is_file(),
        "valid": False,
        "problems": [],
        "production_blockers": [],
    }
    if not path.is_file():
        entry["problems"].append("evidence file is missing")
        return entry
    entry["size_bytes"] = path.stat().st_size
    entry["sha256"] = _sha256_file(path)
    if path.stat().st_size == 0:
        entry["problems"].append("evidence file is empty")
        return entry
    try:
        if kind == "ci_report":
            valid, summary, problems, production_blockers = _inspect_ci_report(_read_json(path))
        elif kind == "sbom":
            valid, summary, problems, production_blockers = _inspect_sbom(_read_json(path))
        elif kind in {value[0] for value in SPECIAL_EVIDENCE_INSPECTORS.values()}:
            inspector = next(
                inspector
                for inspector_kind, inspector in SPECIAL_EVIDENCE_INSPECTORS.values()
                if inspector_kind == kind
            )
            valid, summary, problems, production_blockers = inspector(_read_json(path))
        else:
            valid, summary, problems, production_blockers = True, {}, [], []
    except (OSError, ValueError) as exc:
        valid, summary, problems, production_blockers = False, {}, [str(exc)], []
    entry["valid"] = valid
    entry["summary"] = summary
    entry["problems"].extend(problems)
    entry["production_blockers"].extend(production_blockers)
    return entry


def _git_revision(root: Path) -> tuple[str | None, bool | None]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
        tracked_status = subprocess.run(
            ["git", "status", "--short", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return revision or None, not bool(tracked_status)


def build_release_manifest(
    *,
    ci_report: Path,
    sbom: Path,
    additional: list[tuple[str, Path]] | None = None,
    project_root: Path | None = None,
) -> dict:
    root = project_root or Path.cwd()
    revision, tracked_clean = _git_revision(root)
    evidence = [
        _evidence_entry("ci", ci_report, "ci_report"),
        _evidence_entry("sbom", sbom, "sbom"),
    ]
    seen = {"ci", "sbom"}
    for label, path in additional or []:
        if not label or label in seen:
            raise ValueError(f"evidence label is empty or duplicated: {label!r}")
        seen.add(label)
        kind = SPECIAL_EVIDENCE_INSPECTORS.get(label, ("additional", None))[0]
        evidence.append(_evidence_entry(label, path, kind))
    problems = [
        f"{entry['label']}: {problem}"
        for entry in evidence
        for problem in entry.get("problems", [])
    ]
    if tracked_clean is False:
        problems.append("tracked working tree contains uncommitted changes")
    evidence_labels = {entry["label"] for entry in evidence}
    production_blockers = [
        f"required production evidence is missing: {label}"
        for label in sorted(REQUIRED_PRODUCTION_EVIDENCE - evidence_labels)
    ]
    production_blockers.extend(
        f"{entry['label']}: {blocker}"
        for entry in evidence
        for blocker in entry.get("production_blockers", [])
    )
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_revision": revision,
        "tracked_worktree_clean": tracked_clean,
        "local_evidence_complete": not problems,
        "problems": problems,
        "production_evidence_ready": not problems and not production_blockers,
        "production_blockers": production_blockers,
        "evidence": evidence,
        "limitations": [
            "This index verifies local files only; it does not prove production deployment.",
            "Frontend build and end-to-end tests are enforced by separate CI jobs.",
            "Operational approvals and environment-specific acceptance remain external evidence.",
        ],
    }


def write_manifest(payload: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _parse_additional(values: list[str]) -> list[tuple[str, Path]]:
    parsed = []
    for value in values:
        label, separator, raw_path = value.partition("=")
        if not separator or not label.strip() or not raw_path.strip():
            raise ValueError("--evidence must use LABEL=PATH")
        parsed.append((label.strip(), Path(raw_path.strip())))
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Query Pilot release evidence index")
    parser.add_argument("--ci-report", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--evidence", action="append", default=[], help="additional LABEL=PATH evidence")
    parser.add_argument("--output", type=Path, default=Path("artifacts/release-evidence.json"))
    parser.add_argument(
        "--require-production-evidence",
        action="store_true",
        help="return 1 when validated evidence still contains production blockers",
    )
    args = parser.parse_args(argv)
    try:
        additional = _parse_additional(args.evidence)
        protected = {args.ci_report.resolve(), args.sbom.resolve()}
        protected.update(path.resolve() for _, path in additional)
        if args.output.resolve() in protected:
            raise ValueError("output path must not overwrite an evidence file")
        payload = build_release_manifest(
            ci_report=args.ci_report,
            sbom=args.sbom,
            additional=additional,
        )
        write_manifest(payload, args.output)
    except (OSError, ValueError) as exc:
        print(f"Release evidence failed: {exc}")
        return 2
    print(f"Release evidence written: {args.output}")
    if not payload["local_evidence_complete"]:
        print("Release evidence is incomplete or blocked:")
        for problem in payload["problems"]:
            print(f"- {problem}")
    if payload["production_blockers"]:
        print("Production evidence remains blocked:")
        for blocker in payload["production_blockers"]:
            print(f"- {blocker}")
    if not payload["local_evidence_complete"]:
        return 1
    print("Release evidence is complete for the supplied local checks.")
    if args.require_production_evidence and not payload["production_evidence_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
