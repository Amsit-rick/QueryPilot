import asyncio
import json
from pathlib import Path

from app.business_gold_evaluation import evaluate_business_gold, load_business_gold_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _dataset(case):
    return {
        "format": "query-pilot-business-gold-dataset",
        "dataset_version": "business-gold-test-v1",
        "synthetic": True,
        "cases": [case],
    }


def _case(**overrides):
    payload = {
        "id": "regional-sales",
        "category": "aggregation",
        "question": "各区域销售额",
        "gold_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY region",
        "expected_columns": ["region", "total_amount"],
        "expected_rows": [["华东", 1960.0], ["华南", 980.0]],
    }
    payload.update(overrides)
    return payload


def test_business_gold_validates_question_sql_and_result_without_external_model():
    report = asyncio.run(evaluate_business_gold(dataset=_dataset(_case()), generate=None))

    assert report["local_gate"]["passed"] is True
    assert report["external_validation"] == "not_run"
    assert report["metrics"]["gold_integrity_rate"] == 1.0
    assert report["metrics"]["result_exact_match_rate"] is None
    assert report["cases"][0]["gold_status"] == "passed"
    assert report["cases"][0]["model_status"] == "not_run"
    assert "expected_rows" not in report["cases"][0]


def test_business_gold_current_model_result_match_and_guardrail_failure():
    async def correct(**_kwargs):
        return {"sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY region"}

    passed = asyncio.run(evaluate_business_gold(dataset=_dataset(_case()), generate=correct))
    assert passed["external_validation"] == "passed"
    assert passed["external_gate"]["passed"] is True
    assert passed["metrics"]["result_exact_match_rate"] == 1.0
    assert passed["cases"][0]["model_status"] == "passed"

    async def unsafe(**_kwargs):
        return {"sql": "DELETE FROM sales"}

    rejected = asyncio.run(evaluate_business_gold(dataset=_dataset(_case()), generate=unsafe))
    assert rejected["external_validation"] == "failed"
    assert rejected["external_gate"]["passed"] is False
    assert rejected["cases"][0]["model_status"] == "rejected"
    assert rejected["cases"][0]["error_code"] == "E_READONLY_VIOLATION"


def test_business_gold_fails_local_gate_when_gold_result_is_wrong():
    report = asyncio.run(evaluate_business_gold(
        dataset=_dataset(_case(expected_rows=[["华东", 1.0]])),
        generate=None,
    ))

    assert report["local_gate"]["passed"] is False
    assert report["local_gate"]["reason_codes"] == ["GOLD_INTEGRITY_FAILURE"]
    assert report["cases"][0]["gold_status"] == "failed"


def test_versioned_business_gold_dataset_covers_complex_relational_cases():
    dataset = load_business_gold_dataset(PROJECT_ROOT / "data" / "skill_query_eval.json")
    categories = {case["category"] for case in dataset["cases"]}

    assert dataset["format"] == "query-pilot-business-gold-dataset"
    assert dataset["dataset_version"] == "business-gold-synthetic-v1"
    assert dataset["synthetic"] is True
    assert len(dataset["cases"]) == 11
    assert {"aggregation", "relational_join", "join_fanout", "multi_fact_preaggregation"} <= categories
    assert all("gold_sql" in case and "expected_rows" in case for case in dataset["cases"])
