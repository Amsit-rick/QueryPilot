"""Run the versioned synthetic reflection evaluation set.

This harness is deterministic and does not call a live model or database. It
compares disabled automatic repair, rules-only reflection, and rules plus the
recorded semantic-critic fixture. Synthetic results are not production
business-accuracy evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.reflection import (  # noqa: E402
    ReflectionAction,
    ReflectionBudget,
    ReflectionFailureType,
    ReflectionPolicy,
    ReflectionResult,
)
from app.services.result_quality_service import ResultQualityService  # noqa: E402


_AUTOMATIC_ACTIONS = {
    ReflectionAction.REGENERATE_SQL.value,
    ReflectionAction.RETRIEVE_MORE_SCHEMA.value,
    ReflectionAction.RETRY_EXECUTION.value,
}


def evaluate(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = dataset.get("cases", [])
    modes = {
        mode: _evaluate_mode(cases, mode)
        for mode in ("off", "rules_only", "rules_plus_critic")
    }
    candidate = modes["rules_plus_critic"]
    thresholds = {
        "classification_accuracy_min": 0.9,
        "error_acceptance_rate_max": 0.0,
        "invalid_retry_rate_max": 0.1,
        "security_permission_bypasses_max": 0,
    }
    gate_passed = (
        candidate["classification_accuracy"] >= thresholds["classification_accuracy_min"]
        and candidate["error_acceptance_rate"] <= thresholds["error_acceptance_rate_max"]
        and candidate["invalid_retry_rate"] <= thresholds["invalid_retry_rate_max"]
        and candidate["security_permission_bypasses"] <= thresholds["security_permission_bypasses_max"]
    )
    return {
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "synthetic": bool(dataset.get("synthetic", True)),
        "external_validation": dataset.get("external_validation", "not_run"),
        "case_count": len(cases),
        "modes": modes,
        "gate": {"passed": gate_passed, "thresholds": thresholds},
    }


def _evaluate_mode(cases: list[dict], mode: str) -> dict:
    results = []
    latencies = []
    critic_calls = 0
    for case in cases:
        started = perf_counter()
        predicted, used_critic = _predict(case, mode)
        latencies.append((perf_counter() - started) * 1000)
        critic_calls += int(used_critic)
        expected_action = case["expected_action"]
        expected_failure = case["expected_failure_type"]
        results.append({
            "id": case["id"],
            "expected_action": expected_action,
            "predicted_action": predicted.action.value,
            "expected_failure_type": expected_failure,
            "predicted_failure_type": predicted.failure_type.value,
            "correct": predicted.action.value == expected_action and predicted.failure_type.value == expected_failure,
            "hard_gate": bool(case.get("hard_gate")),
        })

    error_cases = [item for item in results if item["expected_action"] != ReflectionAction.ACCEPT.value]
    accepted_cases = [item for item in results if item["expected_action"] == ReflectionAction.ACCEPT.value]
    repair_cases = [item for item in results if item["expected_action"] in _AUTOMATIC_ACTIONS]
    unsafe_accepts = sum(item["predicted_action"] == ReflectionAction.ACCEPT.value for item in error_cases)
    wrong_rejections = sum(item["predicted_action"] != ReflectionAction.ACCEPT.value for item in accepted_cases)
    invalid_retries = sum(item["predicted_action"] in _AUTOMATIC_ACTIONS and item["expected_action"] not in _AUTOMATIC_ACTIONS for item in results)
    bypasses = sum(item["hard_gate"] and item["predicted_action"] in {ReflectionAction.ACCEPT.value, *_AUTOMATIC_ACTIONS} for item in results)
    return {
        "total": len(results),
        "classification_accuracy": _ratio(sum(item["correct"] for item in results), len(results)),
        "first_success_rate": _ratio(sum(item["predicted_action"] == ReflectionAction.ACCEPT.value for item in accepted_cases), len(accepted_cases)),
        "repair_success_rate": _ratio(sum(item["correct"] for item in repair_cases), len(repair_cases)),
        "error_acceptance_rate": _ratio(unsafe_accepts, len(error_cases)),
        "error_rejection_rate": _ratio(len(error_cases) - unsafe_accepts, len(error_cases)),
        "wrong_rejection_rate": _ratio(wrong_rejections, len(accepted_cases)),
        "invalid_retry_rate": _ratio(invalid_retries, len(results)),
        "average_repairs": round(mean(1 if item["predicted_action"] in _AUTOMATIC_ACTIONS else 0 for item in results), 4) if results else 0.0,
        "latency_ms_avg": round(mean(latencies), 4) if latencies else 0.0,
        "critic_calls": critic_calls,
        "estimated_input_tokens": critic_calls * 180,
        "estimated_output_tokens": critic_calls * 80,
        "estimated_cost_usd": round(critic_calls * 260 / 1000 * 0.00014, 8),
        "security_permission_bypasses": bypasses,
        "results": results,
    }


def _predict(case: dict, mode: str) -> tuple[ReflectionResult, bool]:
    if case["kind"] == "failure":
        policy_result = ReflectionPolicy().from_failure(
            stage=case.get("stage", "validate"),
            error_code=case.get("error_code", "E_QUERY_ERROR"),
            message=case.get("error_code", "E_QUERY_ERROR"),
            retryable=bool(case.get("retryable", False)),
            provided_sql=bool(case.get("provided_sql", False)),
            budget=ReflectionBudget(),
        )
        if mode != "off" or case.get("hard_gate"):
            return policy_result, False
        return _disabled_failure(policy_result.failure_type), False

    assessment = ResultQualityService().assess(
        question=case["question"],
        sql=case["sql"],
        columns=case.get("columns", []),
        rows=case.get("rows", []),
        truncated=bool(case.get("truncated", False)),
        authorized_schema={table: set(columns) for table, columns in case.get("authorized_schema", {}).items()},
        relationships=case.get("relationships", []),
        schema_details=case.get("schema_details", {}),
        expected_dimensions=case.get("expected_dimensions", []),
        expected_metrics=case.get("expected_metrics", []),
        explain_summary=case.get("explain_summary", {}),
    )
    if mode == "off":
        return _accepted("REFLECTION_DISABLED"), False
    if mode == "rules_plus_critic" and assessment.outcome == "uncertain" and case.get("critic_result"):
        return ReflectionResult.model_validate(case["critic_result"]), True
    return assessment.reflection, False


def _accepted(code: str) -> ReflectionResult:
    return ReflectionResult(accepted=True, failure_type=ReflectionFailureType.NONE, reason_codes=[code], action=ReflectionAction.ACCEPT, retryable=False, confidence=1.0)


def _disabled_failure(failure_type: ReflectionFailureType) -> ReflectionResult:
    return ReflectionResult(accepted=False, failure_type=failure_type, reason_codes=["REFLECTION_DISABLED"], action=ReflectionAction.FAIL, retryable=False, confidence=1.0)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "data" / "reflection_eval.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.dataset)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

