import json
from pathlib import Path

from app.services.reflection_mode_service import resolve_reflection_mode
from scripts.evaluate_reflection import evaluate


def test_reflection_evaluation_is_versioned_and_reports_all_modes():
    report = evaluate(Path("data/reflection_eval.json"))

    assert report["dataset_version"] == "reflection-eval-v1"
    assert set(report["modes"]) == {"off", "rules_only", "rules_plus_critic"}
    assert report["modes"]["rules_plus_critic"]["security_permission_bypasses"] == 0
    assert report["gate"]["passed"] is True
    assert report["external_validation"] == "not_run"


def test_enforced_mode_requires_passing_versioned_gate(tmp_path):
    missing = resolve_reflection_mode("enforced", report_path=tmp_path / "missing.json")
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps({"dataset_version": "reflection-eval-v1", "gate": {"passed": False}}), encoding="utf-8")
    bad = resolve_reflection_mode("enforced", report_path=bad_path)
    good_path = tmp_path / "good.json"
    good_path.write_text(json.dumps({
        "format": "query-pilot-reflection-release-gate",
        "version": 1,
        "passed": True,
        "reason_codes": [],
        "inputs": {
            "action": {"dataset_version": "reflection-eval-v1", "passed": True, "sha256": "a" * 64},
            "result": {"dataset_version": "reflection-result-eval-v1", "passed": True, "sha256": "b" * 64},
            "security": {"dataset_version": "reflection-security-eval-v1", "passed": True, "sha256": "c" * 64},
        },
        "metrics": {"final_result_accuracy": 1.0, "security_permission_bypasses": 0, "sensitive_value_leaks": 0, "dangerous_actions": 0},
    }), encoding="utf-8")
    good = resolve_reflection_mode("enforced", report_path=good_path)

    assert missing.effective_mode == "shadow" and missing.gate_passed is False
    assert bad.effective_mode == "shadow" and bad.gate_passed is False
    assert good.effective_mode == "enforced" and good.gate_passed is True

