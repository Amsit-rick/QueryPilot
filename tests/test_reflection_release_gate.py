import json

from app.services.reflection_mode_service import resolve_reflection_mode
from scripts.build_reflection_release_gate import build_release_gate


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _reports(tmp_path):
    action = _write(
        tmp_path / "action.json",
        {
            "dataset_version": "reflection-eval-v1",
            "gate": {"passed": True},
            "modes": {"rules_plus_critic": {"action_accuracy": 1.0, "security_permission_bypasses": 0}},
        },
    )
    result = _write(
        tmp_path / "result.json",
        {
            "dataset_version": "reflection-result-eval-v1",
            "gate": {"passed": True},
            "metrics": {"final_result_accuracy": 0.99, "security_permission_bypasses": 0},
        },
    )
    security = _write(
        tmp_path / "security.json",
        {
            "dataset_version": "reflection-security-eval-v1",
            "gate": {"passed": True},
            "security_permission_bypasses": 0,
            "sensitive_value_leaks": 0,
            "dangerous_actions": 0,
        },
    )
    return action, result, security


def test_composite_release_gate_requires_all_three_reports(tmp_path):
    action, result, security = _reports(tmp_path)
    payload = build_release_gate(action_path=action, result_path=result, security_path=security)

    assert payload["format"] == "query-pilot-reflection-release-gate"
    assert payload["version"] == 1
    assert payload["passed"] is True
    assert set(payload["inputs"]) == {"action", "result", "security"}
    assert all(item["sha256"] for item in payload["inputs"].values())


def test_composite_release_gate_fails_closed_on_result_or_security_regression(tmp_path):
    action, result, security = _reports(tmp_path)
    _write(
        result,
        {
            "dataset_version": "reflection-result-eval-v1",
            "gate": {"passed": True},
            "metrics": {"final_result_accuracy": 0.94, "security_permission_bypasses": 0},
        },
    )
    _write(
        security,
        {
            "dataset_version": "reflection-security-eval-v1",
            "gate": {"passed": True},
            "security_permission_bypasses": 0,
            "sensitive_value_leaks": 1,
            "dangerous_actions": 0,
        },
    )

    payload = build_release_gate(action_path=action, result_path=result, security_path=security)

    assert payload["passed"] is False
    assert "RESULT_ACCURACY_BELOW_THRESHOLD" in payload["reason_codes"]
    assert "SENSITIVE_VALUE_LEAK" in payload["reason_codes"]


def test_runtime_enforced_mode_accepts_only_composite_gate(tmp_path):
    action, result, security = _reports(tmp_path)
    legacy = _write(
        tmp_path / "legacy.json",
        {
            "dataset_version": "reflection-eval-v1",
            "gate": {"passed": True},
            "modes": {"rules_plus_critic": {"security_permission_bypasses": 0}},
        },
    )
    release = _write(
        tmp_path / "release.json",
        build_release_gate(action_path=action, result_path=result, security_path=security),
    )

    legacy_decision = resolve_reflection_mode("enforced", report_path=legacy)
    release_decision = resolve_reflection_mode("enforced", report_path=release)

    assert legacy_decision.effective_mode == "shadow"
    assert legacy_decision.gate_passed is False
    assert release_decision.effective_mode == "enforced"
    assert release_decision.gate_passed is True


def test_runtime_gate_fails_closed_on_invalid_metric_types(tmp_path):
    action, result, security = _reports(tmp_path)
    payload = build_release_gate(action_path=action, result_path=result, security_path=security)
    payload["metrics"]["final_result_accuracy"] = "not-a-number"
    path = _write(tmp_path / "invalid-metrics.json", payload)

    decision = resolve_reflection_mode("enforced", report_path=path)

    assert decision.effective_mode == "shadow"
    assert decision.gate_passed is False

