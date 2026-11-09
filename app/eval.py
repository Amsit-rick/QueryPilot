"""Offline, versioned evaluation for the controlled query pipeline.

The evaluator reports separate evidence for guardrail validation, read-only
execution, model-generation fixtures, and declared business expectations.
Validation success is never presented as proof that a live model understood a
business question.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .db import execute_readonly
from .guardrails import QueryError, validate_sql

DEFAULT_SCHEMA = {
    "sales": {"id", "region", "amount", "sold_at"},
    "customers": {"id", "name", "region"},
}


def _metric(values: list[bool | None]) -> dict[str, Any]:
    evaluated = [value for value in values if value is not None]
    passed = sum(1 for value in evaluated if value)
    return {"evaluated": len(evaluated), "passed": passed, "accuracy": passed / len(evaluated) if evaluated else None}


def _evaluate_item(item: dict[str, Any], schema: dict[str, set[str]], db_path: str) -> dict[str, Any]:
    expected_error = item.get("error_code")
    expected_sql = str(item.get("sql", ""))
    result: dict[str, Any] = {
        "id": item.get("id", "unknown"),
        "category": item.get("category", "uncategorized"),
        "question": item.get("question", ""),
        "sql_valid": None,
        "guardrail_passed": None,
        "execution_success": None,
        "model_generation_success": None,
        "business_correct": None,
        "passed": False,
    }
    try:
        normalized = validate_sql(expected_sql, schema)
        result["sql_valid"] = True
        result["guardrail_passed"] = expected_error is None
    except QueryError as exc:
        result["error"] = exc.code
        result["sql_valid"] = False
        result["guardrail_passed"] = expected_error == exc.code
        result["passed"] = result["guardrail_passed"]
        if expected_error is None:
            result["failure_reason"] = "unexpected_validation_error"
        return result

    if expected_error:
        result["error"] = "NOT_REJECTED"
        result["failure_reason"] = "expected_rejection_not_triggered"
        return result

    try:
        columns, rows, truncated = execute_readonly(
            db_path,
            normalized,
            max_rows=int(item.get("max_rows", 1000)),
            params=item.get("params") or {},
        )
        result.update({"execution_success": True, "columns": columns, "row_count": len(rows), "truncated": truncated})
    except Exception as exc:
        result["execution_success"] = False
        result["error"] = getattr(exc, "code", "E_EXECUTION_ERROR")
        result["failure_reason"] = "execution_failed"
        return result

    if "expected_columns" in item:
        result["business_correct"] = columns == list(item["expected_columns"])
    if "expected_rows" in item and result["business_correct"] is not False:
        result["business_correct"] = rows == item["expected_rows"]
    if result["business_correct"] is False:
        result["failure_reason"] = "business_expectation_mismatch"

    candidate = item.get("generated_sql")
    if candidate is not None:
        try:
            result["model_generation_success"] = validate_sql(str(candidate), schema) == normalized
        except QueryError:
            result["model_generation_success"] = False
        if result["model_generation_success"] is False:
            result["failure_reason"] = result.get("failure_reason", "model_generation_mismatch")

    result["passed"] = bool(result["guardrail_passed"] and result["execution_success"] and result["business_correct"] is not False)
    return result


def run_eval(path: str = "./data/eval.json", *, db_path: str = "./data/demo.db") -> dict[str, Any]:
    dataset = json.loads(Path(path).read_text(encoding="utf-8"))
    sections: dict[str, list[dict[str, Any]]] = {}
    all_results: list[dict[str, Any]] = []
    for section in ("golden", "boundary", "adversarial"):
        items = [_evaluate_item(item, DEFAULT_SCHEMA, db_path) for item in dataset.get(section, [])]
        sections[section] = items
        all_results.extend(items)

    failures = Counter(
        item.get("failure_reason") or item.get("error")
        for item in all_results
        if not item.get("passed")
    )
    passed = sum(1 for item in all_results if item["passed"])
    report = {
        "dataset_version": dataset["version"],
        "total": len(all_results),
        "passed": passed,
        "accuracy": passed / len(all_results) if all_results else 0,
        "sections": {
            name: {
                "total": len(items),
                "passed": sum(1 for item in items if item["passed"]),
                "accuracy": sum(1 for item in items if item["passed"]) / len(items) if items else 0,
                "results": items,
            }
            for name, items in sections.items()
        },
        "metrics": {
            "sql_validity": _metric([item["sql_valid"] for item in all_results]),
            "guardrail": _metric([item["guardrail_passed"] for item in all_results]),
            "execution": _metric([item["execution_success"] for item in all_results]),
            "model_generation": _metric([item["model_generation_success"] for item in all_results]),
            "business_correctness": _metric([item["business_correct"] for item in all_results]),
        },
        "failure_attribution": dict(failures),
        "results": all_results,
    }
    from .capability_eval import run_capability_eval

    report["cross_capability"] = run_capability_eval()
    return report


if __name__ == "__main__":
    print(json.dumps(run_eval(), ensure_ascii=False, indent=2))
