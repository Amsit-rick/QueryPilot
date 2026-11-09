from pathlib import Path

import yaml

import json

from scripts.validate_alert_rules import build_report, main, validate_rules


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_alert_rules_are_structurally_valid():
    assert validate_rules(PROJECT_ROOT / "deploy" / "prometheus-alerts.yml") == []


def test_project_alert_rules_cover_agent_reflection_recovery_and_trace_slos():
    payload = yaml.safe_load((PROJECT_ROOT / "deploy" / "prometheus-alerts.yml").read_text(encoding="utf-8"))
    names = {
        rule["alert"]
        for group in payload["groups"]
        for rule in group.get("rules", [])
    }

    assert {
        "QueryPilotReflectionSafetyRollback",
        "QueryPilotReflectionRepairFailureSpike",
        "QueryPilotCheckpointRecoveryFailures",
        "QueryPilotReflectionLatencyHigh",
        "QueryPilotCriticFailures",
        "QueryPilotBudgetExhaustion",
        "QueryPilotSchemaEmbeddingFailures",
    } <= names


def test_prometheus_config_loads_project_alert_rules_and_expected_job():
    config = yaml.safe_load((PROJECT_ROOT / "deploy" / "prometheus.yml").read_text(encoding="utf-8"))

    assert "prometheus-alerts.yml" in config["rule_files"]
    assert any(item.get("job_name") == "query-pilot-api" for item in config["scrape_configs"])


def test_alert_rules_reject_unknown_metric_and_unconfirmed_threshold(tmp_path):
    rules = tmp_path / "alerts.yml"
    rules.write_text(
        """groups:
  - name: invalid
    rules:
      - alert: InvalidMetric
        expr: query_pilot_unknown_total > 1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: invalid
          runbook_url: runbook.md
""",
        encoding="utf-8",
    )

    problems = validate_rules(rules)

    assert any("unknown metrics" in problem for problem in problems)
    assert any("enterprise confirmation" in problem for problem in problems)


def test_alert_rules_reject_duplicate_names(tmp_path):
    rules = tmp_path / "alerts.yml"
    rules.write_text(
        """groups:
  - name: duplicate
    rules:
      - &base
        alert: SameAlert
        expr: query_pilot_errors_total > 1
        for: 5m
        labels: {severity: warning, threshold_status: needs_enterprise_confirmation}
        annotations: {summary: first, runbook_url: runbook.md}
      - <<: *base
        annotations: {summary: second, runbook_url: runbook.md}
""",
        encoding="utf-8",
    )

    assert any("duplicate alert name" in problem for problem in validate_rules(rules))


def test_alert_report_distinguishes_local_validation_from_promtool(tmp_path):
    rules = tmp_path / "alerts.yml"
    rules.write_text(
        """groups:
  - name: valid
    rules:
      - alert: Example
        expr: query_pilot_errors_total > 1
        for: 5m
        labels: {severity: warning, threshold_status: needs_enterprise_confirmation}
        annotations: {summary: example, runbook_url: runbook.md}
""",
        encoding="utf-8",
    )

    report = build_report(rules)

    assert report["local_validation"] == "passed"
    assert report["rule_count"] == 1
    assert report["official_promtool_executed"] is False
    assert report["notification_chain_tested"] is False


def test_alert_report_is_written_for_invalid_rules(tmp_path):
    rules = tmp_path / "alerts.yml"
    report_path = tmp_path / "artifacts" / "alerts.json"
    rules.write_text("groups: []\n", encoding="utf-8")

    assert main(["--rules", str(rules), "--report", str(report_path)]) == 1
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["local_validation"] == "failed"
