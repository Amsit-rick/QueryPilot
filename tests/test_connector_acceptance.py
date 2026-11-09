import json
import subprocess

import pytest

from scripts.connector_acceptance import build_report, write_report


CONFIG = {
    "ORACLE_DSN": "db.internal:1521/service",
    "ORACLE_USER": "acceptance",
    "ORACLE_PASSWORD": "oracle-secret",
    "ORACLE_SCHEMA_OWNER": "ACCEPTANCE_OWNER",
    "ORACLE_PLAN_USER": "acceptance_plan",
    "ORACLE_PLAN_PASSWORD": "oracle-plan-secret",
    "SQLSERVER_HOST": "sql.internal",
    "SQLSERVER_DATABASE": "acceptance",
    "SQLSERVER_USER": "acceptance",
    "SQLSERVER_PASSWORD": "sqlserver-secret",
    "CLICKHOUSE_HOST": "clickhouse.internal",
    "CLICKHOUSE_DATABASE": "acceptance",
    "CLICKHOUSE_USER": "acceptance",
    "CLICKHOUSE_PASSWORD": "clickhouse-secret",
}


def _completed(command, returncode=0, stdout="1 passed in 0.01s", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_missing_configuration_blocks_without_running_and_redacts_values(tmp_path):
    calls = []

    def runner(command, cwd, env, timeout):
        calls.append(command)
        return _completed(command)

    report = build_report(
        "oracle",
        root=tmp_path,
        environ={"ORACLE_PASSWORD": "must-never-appear"},
        runner=runner,
        module_checker=lambda _: True,
    )

    assert calls == []
    assert report["passed"] is False
    assert report["results"][0]["blocking_reason"] == "missing_configuration"
    assert "must-never-appear" not in json.dumps(report)


def test_missing_driver_is_distinct_from_missing_configuration(tmp_path):
    report = build_report(
        "oracle",
        root=tmp_path,
        environ=CONFIG,
        runner=lambda *_: pytest.fail("runner must not be called"),
        module_checker=lambda _: False,
    )

    result = report["results"][0]
    assert report["version"] == 8
    assert result["configured"] is True
    assert result["driver_installed"] is False
    assert result["executed"] is False
    assert result["blocking_reason"] == "driver_not_installed"


def test_passing_connector_runs_only_its_stable_live_case(tmp_path):
    calls = []

    def runner(command, cwd, env, timeout):
        calls.append((command, cwd, env, timeout))
        return _completed(command)

    report = build_report(
        "sqlserver",
        root=tmp_path,
        environ=CONFIG,
        runner=runner,
        module_checker=lambda _: True,
        timeout_seconds=45,
    )

    result = report["results"][0]
    assert result["passed"] is True
    assert result["external_validation"] == "passed"
    assert result["pytest_summary"] == "8_passed"
    assert all(result["evidence"].values())
    assert result["evidence"]["cte_preaggregation"] is True
    assert set(result["capability_gaps"]) == {"cost_estimate"}
    assert result["capability_implementation"] == {
        "schema_details": True,
        "relationships": True,
        "cost_estimate": False,
        "cte_preaggregation": True,
    }
    assert result["exit_code"] == 0
    assert result["duration_ms"] >= 0
    assert len(calls) == 8
    for command, cwd, env, timeout in calls:
        assert command[3].endswith("[sqlserver]")
        assert cwd == tmp_path.resolve()
        assert env["RUN_SQLSERVER_TESTS"] == "1"
        assert timeout == 45


def test_oracle_acceptance_adds_real_cost_estimate_evidence(tmp_path):
    calls = []

    def runner(command, cwd, env, timeout):
        calls.append(command)
        return _completed(command)

    report = build_report(
        "oracle",
        root=tmp_path,
        environ=CONFIG,
        runner=runner,
        module_checker=lambda _: True,
    )

    result = report["results"][0]
    assert result["pytest_summary"] == "9_passed"
    assert result["evidence"]["cost_estimate"] is True
    assert result["capability_implementation"]["cost_estimate"] is True
    assert result["capability_gaps"] == []
    assert len(calls) == 9
    assert any("test_remote_cost_estimate[oracle]" in command[3] for command in calls)


def test_partial_failure_preserves_granular_live_evidence(tmp_path):
    def runner(command, cwd, env, timeout):
        if "test_remote_cte_preaggregation" in command[3]:
            return _completed(command, returncode=1, stdout="1 failed in 0.01s")
        return _completed(command)

    report = build_report(
        "oracle",
        root=tmp_path,
        environ=CONFIG,
        runner=runner,
        module_checker=lambda _: True,
    )

    result = report["results"][0]
    assert result["passed"] is False
    assert result["executed"] is True
    assert result["pytest_summary"] == "8_passed,1_failed"
    assert result["evidence"]["cte_preaggregation"] is False
    assert all(
        passed
        for evidence, passed in result["evidence"].items()
        if evidence != "cte_preaggregation"
    )


def test_failure_report_keeps_only_counts_and_never_raw_output(tmp_path):
    def runner(command, cwd, env, timeout):
        return _completed(
            command,
            returncode=1,
            stdout="1 failed in 0.02s",
            stderr="connection to sql.internal failed with password=sqlserver-secret",
        )

    report = build_report(
        "sqlserver",
        root=tmp_path,
        environ=CONFIG,
        runner=runner,
        module_checker=lambda _: True,
    )

    result = report["results"][0]
    rendered = json.dumps(report)
    assert result["executed"] is True
    assert result["passed"] is False
    assert result["external_validation"] == "failed"
    assert result["pytest_summary"] == "8_failed"
    assert not any(result["evidence"].values())
    assert result["exit_code"] == 1
    assert result["duration_ms"] >= 0
    assert "sql.internal" not in rendered
    assert "sqlserver-secret" not in rendered


def test_skipped_pytest_result_cannot_be_reported_as_passed(tmp_path):
    def runner(command, cwd, env, timeout):
        return _completed(command, stdout="1 skipped in 0.01s")

    report = build_report(
        "clickhouse",
        root=tmp_path,
        environ=CONFIG,
        runner=runner,
        module_checker=lambda _: True,
    )

    assert report["results"][0]["passed"] is False
    assert report["results"][0]["pytest_summary"] == "8_skipped"


def test_report_writer_replaces_target_with_machine_readable_json(tmp_path):
    target = tmp_path / "nested" / "connector-report.json"
    report = {"format": "query-pilot-connector-acceptance", "secrets_redacted": True}

    write_report(report, target)

    assert json.loads(target.read_text(encoding="utf-8")) == report
    assert not (target.parent / f".{target.name}.tmp").exists()
