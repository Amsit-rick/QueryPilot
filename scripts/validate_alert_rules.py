"""Validate Query Pilot Prometheus rules without contacting Prometheus."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

KNOWN_METRICS = {
    "query_pilot_requests_total",
    "query_pilot_errors_total",
    "query_pilot_audit_write_failures_total",
    "query_pilot_request_duration_seconds_bucket",
    "query_pilot_cost_usd_total",
    "query_pilot_input_tokens_total",
    "query_pilot_output_tokens_total",
    "query_pilot_rows_returned_total",
    "query_pilot_truncated_queries_total",
    "query_pilot_large_result_queries_total",
    "query_pilot_reflection_decisions_total",
    "query_pilot_reflection_repair_success_total",
    "query_pilot_reflection_repair_failure_total",
    "query_pilot_reflection_invalid_retry_total",
    "query_pilot_reflection_rollbacks_total",
    "query_pilot_checkpoint_recovery_failures_total",
    "query_pilot_budget_exhausted_total",
    "query_pilot_critic_timeouts_total",
    "query_pilot_critic_invalid_responses_total",
    "query_pilot_agent_stage_duration_seconds_bucket",
    "query_pilot_schema_embedding_requests_total",
}
SEVERITIES = {"warning", "critical"}
REPORT_FORMAT = "query-pilot-alert-rules-validation"
REPORT_VERSION = 1


def validate_rules(path: Path) -> list[str]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"rules file not found: {path}"]
    except (OSError, yaml.YAMLError) as exc:
        return [f"rules file is not valid YAML: {exc}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        return ["rules file must contain a groups list"]
    problems = []
    alert_names = set()
    rule_count = 0
    for group_index, group in enumerate(payload["groups"]):
        if not isinstance(group, dict) or not isinstance(group.get("rules"), list):
            problems.append(f"groups[{group_index}] must contain a rules list")
            continue
        for rule_index, rule in enumerate(group["rules"]):
            rule_count += 1
            prefix = f"groups[{group_index}].rules[{rule_index}]"
            if not isinstance(rule, dict):
                problems.append(f"{prefix} must be an object")
                continue
            name = rule.get("alert")
            expression = rule.get("expr")
            labels = rule.get("labels")
            annotations = rule.get("annotations")
            if not isinstance(name, str) or not name.strip():
                problems.append(f"{prefix}.alert is required")
            elif name in alert_names:
                problems.append(f"duplicate alert name: {name}")
            else:
                alert_names.add(name)
            if not isinstance(expression, str) or not expression.strip():
                problems.append(f"{prefix}.expr is required")
            else:
                referenced = set(re.findall(r"query_pilot_[a-z0-9_]+", expression))
                unknown = sorted(referenced - KNOWN_METRICS)
                if unknown:
                    problems.append(f"{name or prefix} references unknown metrics: {', '.join(unknown)}")
            if not isinstance(rule.get("for"), str) or not re.fullmatch(r"\d+[smhd]", rule["for"]):
                problems.append(f"{name or prefix} must define a simple for duration")
            if not isinstance(labels, dict) or labels.get("severity") not in SEVERITIES:
                problems.append(f"{name or prefix} must define warning/critical severity")
            if not isinstance(labels, dict) or labels.get("threshold_status") != "needs_enterprise_confirmation":
                problems.append(f"{name or prefix} must mark thresholds as needing enterprise confirmation")
            if not isinstance(annotations, dict) or not annotations.get("summary"):
                problems.append(f"{name or prefix} must define a summary")
            if not isinstance(annotations, dict) or not annotations.get("runbook_url"):
                problems.append(f"{name or prefix} must define a runbook_url")
    if rule_count == 0:
        problems.append("rules file must contain at least one alert")
    return problems


def build_report(path: Path) -> dict:
    """Build an explicit local report without claiming promtool success."""
    problems = validate_rules(path)
    rule_count = 0
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rule_count = sum(
                len(group.get("rules", []))
                for group in payload.get("groups", [])
                if isinstance(group, dict) and isinstance(group.get("rules"), list)
            )
    except (OSError, yaml.YAMLError):
        pass
    return {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "rules_file": path.name,
        "rule_count": rule_count,
        "local_validation": "passed" if not problems else "failed",
        "problems": problems,
        "threshold_status": "needs_enterprise_confirmation",
        "official_promtool_executed": False,
        "notification_chain_tested": False,
        "limitations": [
            "This report validates YAML structure and repository metric references only.",
            "Run promtool and a real notification drill in the target Prometheus environment.",
        ],
    }


def write_report(payload: dict, target: Path) -> None:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Query Pilot Prometheus alert rules")
    parser.add_argument("--rules", type=Path, default=Path("deploy/prometheus-alerts.yml"))
    parser.add_argument("--report", type=Path, help="write a machine-readable local validation report")
    args = parser.parse_args(argv)
    problems = validate_rules(args.rules)
    if args.report:
        write_report(build_report(args.rules), args.report)
    if problems:
        print("Prometheus alert validation failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"Prometheus alert rules validated: {args.rules}")
    if args.report:
        print(f"Alert validation report written: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
