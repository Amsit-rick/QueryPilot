"""Evaluate reflection actions and repaired result correctness on synthetic data."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.reflection import ReflectionAction, ReflectionBudget, ReflectionPolicy  # noqa: E402
from app.services.result_quality_service import ResultQualityService  # noqa: E402


FORMAT = "query-pilot-reflection-result-eval"
AUTO_ACTIONS = {"regenerate_sql", "retrieve_more_schema", "retry_execution"}


def _render(value: str, variant: dict[str, object]) -> str:
    return value.format(**variant)


def _variant(index: int) -> dict[str, object]:
    return {
        "threshold": (index % 10) * 200,
        "limit": (index % 7) + 1,
        "month": f"2026-{(index % 5) + 1:02d}",
    }


def _default_question(scenario: str, variant: dict[str, object]) -> str:
    if scenario == "top_n":
        return f"销售额最高的前 {variant['limit']} 个区域"
    if scenario == "fanout":
        return "按客户统计订单金额和回款金额"
    if scenario == "empty_result":
        return f"统计 {variant['month']} 之后的区域销售额"
    if scenario == "ambiguity":
        return "统计当前有效订单销售额"
    return "按区域统计销售额"


def _schema() -> dict[str, set[str]]:
    return {
        "sales": {"id", "region", "amount", "sold_at", "status", "deleted_at"},
        "customers": {"id", "name", "credit_limit"},
        "orders": {"id", "customer_id", "amount", "status"},
        "payments": {"id", "customer_id", "amount"},
    }


def _database(dataset: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for statement in dataset["seed_sql"]:
        conn.execute(statement)
    for table, rows in dataset["seed_rows"].items():
        if not rows:
            continue
        placeholders = ",".join("?" for _ in rows[0])
        conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)
    return conn


def _execute(conn: sqlite3.Connection, sql: str) -> tuple[list[str], list[list[object]]]:
    cursor = conn.execute(sql)
    columns = [item[0] for item in cursor.description or ()]
    rows = [list(row) for row in cursor.fetchall()]
    return columns, rows


def _normalized(rows: list[list[object]]) -> list[list[object]]:
    def scalar(value):
        if isinstance(value, float):
            return round(value, 6)
        return value

    return [[scalar(value) for value in row] for row in rows]


def evaluate_result_dataset(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    conn = _database(dataset)
    results = []
    try:
        for family in dataset.get("families", []):
            for index in range(int(family.get("count", 1))):
                variant = _variant(index)
                expected_action = family["expected_action"]
                if family.get("kind") == "failure":
                    reflection = ReflectionPolicy().from_failure(
                        stage=family.get("stage", "validate"),
                        error_code=family.get("error_code", "E_QUERY_ERROR"),
                        message=family.get("error_code", "E_QUERY_ERROR"),
                        retryable=True,
                        provided_sql=False,
                        budget=ReflectionBudget(),
                    )
                    predicted_action = reflection.action.value
                    result_correct = None
                else:
                    question = _render(
                        family.get("question") or _default_question(str(family.get("scenario", "accepted")), variant),
                        variant,
                    )
                    candidate_sql = _render(family["candidate_sql"], variant)
                    columns, rows = _execute(conn, candidate_sql)
                    assessment = ResultQualityService().assess(
                        question=question,
                        sql=candidate_sql,
                        columns=columns,
                        rows=rows,
                        truncated=False,
                        authorized_schema=_schema(),
                        relationships=family.get("relationships", []),
                        schema_details=family.get("schema_details", {}),
                        expected_dimensions=family.get("expected_dimensions", []),
                        expected_metrics=family.get("expected_metrics", []),
                        explain_summary={},
                    )
                    predicted_action = assessment.reflection.action.value
                    execution_sql = family.get("repair_sql") if predicted_action in AUTO_ACTIONS else family.get("candidate_sql")
                    if execution_sql and family.get("gold_sql"):
                        _, actual_rows = _execute(conn, _render(execution_sql, variant))
                        _, gold_rows = _execute(conn, _render(family["gold_sql"], variant))
                        result_correct = _normalized(actual_rows) == _normalized(gold_rows)
                    else:
                        result_correct = None
                hard_gate = bool(family.get("hard_gate"))
                bypass = hard_gate and predicted_action in {"accept", *AUTO_ACTIONS}
                results.append(
                    {
                        "id": f"{family['id']}-{index + 1:03d}",
                        "family": family["id"],
                        "expected_action": expected_action,
                        "predicted_action": predicted_action,
                        "action_correct": predicted_action == expected_action,
                        "result_correct": result_correct,
                        "security_permission_bypass": bypass,
                    }
                )
    finally:
        conn.close()

    executable = [item for item in results if item["result_correct"] is not None]
    action_accuracy = sum(item["action_correct"] for item in results) / len(results) if results else 0.0
    result_accuracy = sum(item["result_correct"] for item in executable) / len(executable) if executable else 0.0
    bypasses = sum(item["security_permission_bypass"] for item in results)
    thresholds = {
        "case_count_min": 100,
        "reflection_action_accuracy_min": 0.95,
        "final_result_accuracy_min": 0.95,
        "security_permission_bypasses_max": 0,
    }
    gate_passed = (
        len(results) >= thresholds["case_count_min"]
        and action_accuracy >= thresholds["reflection_action_accuracy_min"]
        and result_accuracy >= thresholds["final_result_accuracy_min"]
        and bypasses <= thresholds["security_permission_bypasses_max"]
    )
    return {
        "format": FORMAT,
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "synthetic": bool(dataset.get("synthetic", True)),
        "external_validation": dataset.get("external_validation", "not_run"),
        "case_count": len(results),
        "family_count": len({item["family"] for item in results}),
        "metrics": {
            "reflection_action_accuracy": round(action_accuracy, 4),
            "final_result_accuracy": round(result_accuracy, 4),
            "security_permission_bypasses": bypasses,
        },
        "gate": {"passed": gate_passed, "thresholds": thresholds},
        "limitations": [
            "Synthetic deterministic data is not enterprise business-accuracy evidence.",
            "Repair SQL is a versioned fixture, not a live-model generation result.",
        ],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "data" / "reflection_result_eval.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_result_dataset(args.dataset)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

