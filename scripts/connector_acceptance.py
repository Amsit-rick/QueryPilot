"""Run redacted, opt-in acceptance checks for experimental database connectors."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_PYTEST_COUNT = re.compile(r"(?<!\d)(\d+)\s+(passed|failed|skipped|errors?|warnings?)(?!\w)")


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    driver_module: str
    run_flag: str
    required_config: tuple[str, ...]


CONNECTORS = {
    "oracle": ConnectorSpec(
        name="oracle",
        driver_module="oracledb",
        run_flag="RUN_ORACLE_TESTS",
        required_config=(
            "ORACLE_DSN",
            "ORACLE_USER",
            "ORACLE_PASSWORD",
            "ORACLE_SCHEMA_OWNER",
            "ORACLE_PLAN_USER",
            "ORACLE_PLAN_PASSWORD",
        ),
    ),
    "sqlserver": ConnectorSpec(
        name="sqlserver",
        driver_module="pymssql",
        run_flag="RUN_SQLSERVER_TESTS",
        required_config=(
            "SQLSERVER_HOST",
            "SQLSERVER_DATABASE",
            "SQLSERVER_USER",
            "SQLSERVER_PASSWORD",
        ),
    ),
    "clickhouse": ConnectorSpec(
        name="clickhouse",
        driver_module="clickhouse_driver",
        run_flag="RUN_CLICKHOUSE_TESTS",
        required_config=(
            "CLICKHOUSE_HOST",
            "CLICKHOUSE_DATABASE",
            "CLICKHOUSE_USER",
            "CLICKHOUSE_PASSWORD",
        ),
    ),
}

BASE_LIVE_TESTS = {
    "health": "test_remote_health",
    "schema": "test_remote_schema",
    "schema_details": "test_remote_schema_details",
    "preview": "test_remote_preview",
    "parameter_binding": "test_remote_parameter_binding",
    "query": "test_remote_bounded_query",
    "cte_preaggregation": "test_remote_cte_preaggregation",
    "readonly_rejection": "test_remote_readonly_rejection",
}
CONNECTOR_LIVE_TESTS = {
    "oracle": {**BASE_LIVE_TESTS, "cost_estimate": "test_remote_cost_estimate"},
    "sqlserver": dict(BASE_LIVE_TESTS),
    "clickhouse": dict(BASE_LIVE_TESTS),
}
KNOWN_CAPABILITY_GAPS = {
    "oracle": (),
    "sqlserver": ("cost_estimate",),
    "clickhouse": ("cost_estimate", "relationships"),
}
CAPABILITY_IMPLEMENTATION = {
    "oracle": {"schema_details": True, "relationships": True, "cost_estimate": True, "cte_preaggregation": True},
    "sqlserver": {"schema_details": True, "relationships": True, "cost_estimate": False, "cte_preaggregation": True},
    "clickhouse": {"schema_details": True, "relationships": False, "cost_estimate": False, "cte_preaggregation": True},
}

Runner = Callable[[list[str], Path, Mapping[str, str], int], subprocess.CompletedProcess[str]]
ModuleChecker = Callable[[str], bool]


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if not match or match.group(1).startswith("#"):
            continue
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _run(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            # Pytest and database drivers may emit UTF-8 while the Windows
            # console locale is GBK; decoding with the locale crashed the whole
            # acceptance run after the probe had already finished.
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "")


def _pytest_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for raw_count, raw_label in _PYTEST_COUNT.findall(output):
        label = "error" if raw_label in {"error", "errors"} else raw_label.rstrip("s")
        counts[label] = max(counts.get(label, 0), int(raw_count))
    return counts


def _pytest_summary(counts: Mapping[str, int], returncode: int) -> str:
    ordered = ("passed", "failed", "error", "skipped", "warning", "timeout")
    parts = [f"{counts[label]}_{label}" for label in ordered if counts.get(label)]
    if parts:
        return ",".join(parts)
    if returncode == 124:
        return "timeout"
    return f"unrecognized_exit_{returncode}"


def _connector_result(
    spec: ConnectorSpec,
    *,
    root: Path,
    config: Mapping[str, str],
    process_env: Mapping[str, str],
    runner: Runner,
    module_checker: ModuleChecker,
    timeout_seconds: int,
) -> dict:
    missing = [key for key in spec.required_config if not config.get(key, "").strip()]
    configured = not missing
    driver_installed = module_checker(spec.driver_module)
    base = {
        "connector": spec.name,
        "configured": configured,
        "driver_installed": driver_installed,
        "executed": False,
        "passed": False,
        "blocking": True,
        "pytest_summary": "not_run",
        "exit_code": None,
        "duration_ms": 0,
        "external_validation": "not_run",
        "secrets_redacted": True,
        "evidence": {name: False for name in CONNECTOR_LIVE_TESTS[spec.name]},
        "capability_implementation": dict(CAPABILITY_IMPLEMENTATION[spec.name]),
        "capability_gaps": list(KNOWN_CAPABILITY_GAPS[spec.name]),
    }
    if not configured:
        return {**base, "blocking_reason": "missing_configuration"}
    if not driver_installed:
        return {**base, "blocking_reason": "driver_not_installed"}

    test_env = dict(process_env)
    test_env.update(config)
    test_env[spec.run_flag] = "1"
    started = time.monotonic()
    evidence: dict[str, bool] = {}
    total_counts: dict[str, int] = {}
    exit_codes: list[int] = []
    for evidence_name, test_name in CONNECTOR_LIVE_TESTS[spec.name].items():
        command = [
            sys.executable,
            "-m",
            "pytest",
            f"tests/test_remote_gateways_live.py::{test_name}[{spec.name}]",
            "-q",
            "--disable-warnings",
        ]
        completed = runner(command, root, test_env, timeout_seconds)
        exit_codes.append(completed.returncode)
        counts = _pytest_counts(f"{completed.stdout}\n{completed.stderr}")
        for label, count in counts.items():
            total_counts[label] = total_counts.get(label, 0) + count
        if completed.returncode == 124:
            total_counts["timeout"] = total_counts.get("timeout", 0) + 1
        evidence[evidence_name] = (
            completed.returncode == 0
            and counts.get("passed", 0) >= 1
            and counts.get("failed", 0) == 0
            and counts.get("error", 0) == 0
            and counts.get("skipped", 0) == 0
        )
    duration_ms = round((time.monotonic() - started) * 1000)
    passed = all(evidence.values())
    exit_code = next((code for code in exit_codes if code != 0), 0)
    result = {
        **base,
        "executed": True,
        "passed": passed,
        "blocking": not passed,
        "pytest_summary": _pytest_summary(total_counts, exit_code),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "external_validation": "passed" if passed else "failed",
        "evidence": evidence,
        "capability_implementation": dict(CAPABILITY_IMPLEMENTATION[spec.name]),
        "capability_gaps": list(KNOWN_CAPABILITY_GAPS[spec.name]),
    }
    if not passed:
        result["blocking_reason"] = "acceptance_failed"
    return result


def build_report(
    connector: str = "all",
    *,
    root: Path = ROOT,
    environ: Mapping[str, str] | None = None,
    runner: Runner = _run,
    module_checker: ModuleChecker = _module_available,
    timeout_seconds: int = 120,
) -> dict:
    """Build a credential-free report; raw pytest output is never retained."""
    root = root.resolve()
    process_env = dict(os.environ if environ is None else environ)
    config = _read_env(root / ".env")
    config.update(process_env)
    selected = list(CONNECTORS) if connector == "all" else [connector]
    results = [
        _connector_result(
            CONNECTORS[name],
            root=root,
            config=config,
            process_env=process_env,
            runner=runner,
            module_checker=module_checker,
            timeout_seconds=timeout_seconds,
        )
        for name in selected
    ]
    passed = bool(results) and all(item["passed"] for item in results)
    return {
        "format": "query-pilot-connector-acceptance",
        "version": 8,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "selected_connectors": selected,
        "executed": all(item["executed"] for item in results),
        "passed": passed,
        "external_validation": "passed" if passed else "incomplete",
        "blocking_connectors": [item["connector"] for item in results if item["blocking"]],
        "secrets_redacted": True,
        "results": results,
    }


def write_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run experimental connector acceptance checks")
    parser.add_argument("--connector", choices=["all", *CONNECTORS], default="all")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/connector-acceptance.json"),
        help="redacted JSON report path",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="timeout applied to each independent evidence probe",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be at least 1")

    report = build_report(args.connector, timeout_seconds=args.timeout_seconds)
    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    write_report(report, report_path)
    for result in report["results"]:
        status = "PASSED" if result["passed"] else "BLOCKED"
        print(
            f"[{status}] {result['connector']}: executed={str(result['executed']).lower()} "
            f"summary={result['pytest_summary']}"
        )
    print(f"Redacted report: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
