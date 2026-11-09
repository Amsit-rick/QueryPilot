import asyncio

from pathlib import Path

from app.skill_query_evaluation import compare_skill_query_quality, load_skill_query_cases
from app.services.skill_service import BUILTIN_SKILLS


def _candidate() -> list[dict]:
    base = next(item for item in BUILTIN_SKILLS if item.id == "sales-analysis")
    changed = base.model_copy(update={
        "version": "1.1.0",
        "workflow_steps": ("Use exact aggregate for regional sales",),
    })
    return [
        item.model_dump(mode="json") if item.id != changed.id else changed.model_dump(mode="json")
        for item in BUILTIN_SKILLS
    ]


def test_query_quality_eval_compares_validated_rows_for_baseline_and_candidate():
    async def generate(*, question, schema, context, dialect):
        if context.get("skill", {}).get("version") == "1.1.0":
            return {"sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY region"}
        return {"sql": "SELECT region, amount FROM sales ORDER BY region"}

    report = asyncio.run(compare_skill_query_quality(
        baseline=[item.model_dump(mode="json") for item in BUILTIN_SKILLS],
        candidate=_candidate(),
        cases=[{
            "id": "regional-sales",
            "question": "各区域销售额",
            "skill_id": "sales-analysis",
            "gold_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY region",
            "expected_columns": ["region", "total_amount"],
            "expected_rows": [["华东", 1960.0], ["华南", 980.0]],
        }],
        generate=generate,
    ))

    assert report["evaluation_type"] == "skill_sql_result_quality"
    assert report["summary"]["cases"] == 1
    assert report["generation_attempts"] == 2
    assert report["summary"]["candidate"]["generation_success_rate"] == 1.0
    assert report["summary"]["baseline"]["result_exact_match_rate"] == 0.0
    assert report["summary"]["candidate"]["result_exact_match_rate"] == 1.0
    assert report["cases"][0]["candidate"]["status"] == "passed"
    assert report["cases"][0]["baseline"]["sql_valid"] is True


def test_query_quality_eval_rejects_unsafe_model_sql_before_fixture_execution():
    async def generate(*, question, schema, context, dialect):
        return {"sql": "DELETE FROM sales"}

    report = asyncio.run(compare_skill_query_quality(
        baseline=[item.model_dump(mode="json") for item in BUILTIN_SKILLS],
        candidate=_candidate(),
        cases=[{
            "id": "regional-sales",
            "question": "各区域销售额",
            "skill_id": "sales-analysis",
            "gold_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY region",
            "expected_columns": ["region", "total_amount"],
            "expected_rows": [["华东", 1960.0], ["华南", 980.0]],
        }],
        generate=generate,
    ))

    assert report["summary"]["baseline"]["guardrail_pass_rate"] == 0.0
    assert report["summary"]["candidate"]["execution_success_rate"] is None
    assert report["cases"][0]["candidate"]["status"] == "rejected"


def test_synthetic_skill_dataset_covers_joins_dates_and_multi_fact_fanout_cases():
    path = Path(__file__).resolve().parents[1] / "data" / "skill_query_eval.json"
    version, cases = load_skill_query_cases(path)
    categories = {case["category"] for case in cases}

    assert version == "2026-11-04-v3"
    assert len(cases) == 11
    assert {
        "aggregation",
        "relational_join",
        "left_join",
        "date_filter",
        "join_fanout",
        "multi_fact_preaggregation",
    } <= categories
    assert len({case["id"] for case in cases}) == len(cases)


def test_multi_fact_case_proves_naive_join_would_double_count_both_facts():
    path = Path(__file__).resolve().parents[1] / "data" / "skill_query_eval.json"
    _, cases = load_skill_query_cases(path)
    case = next(item for item in cases if item["id"] == "paid-order-item-and-payment-totals")

    assert case["expected_rows"] == [[101, 250.0, 250.0], [102, 100.0, 100.0], [103, 120.0, 120.0], [105, 200.0, 200.0], [107, 80.0, 80.0]]
    assert case["naive_result_rows"][0] == [101, 500.0, 500.0]


def test_all_synthetic_gold_queries_execute_with_exact_expected_rows():
    path = Path(__file__).resolve().parents[1] / "data" / "skill_query_eval.json"
    _, cases = load_skill_query_cases(path)

    async def generate(*, question, schema, context, dialect):
        case = next(item for item in cases if item["question"] == question)
        return {"sql": case["gold_sql"]}

    catalog = [item.model_dump(mode="json") for item in BUILTIN_SKILLS]
    report = asyncio.run(compare_skill_query_quality(
        baseline=catalog,
        candidate=catalog,
        cases=cases,
        generate=generate,
        dataset_version="2026-11-04-v3",
    ))

    assert report["summary"]["cases"] == 11
    assert report["summary"]["baseline"]["result_exact_match_rate"] == 1.0
    assert report["summary"]["candidate"]["result_exact_match_rate"] == 1.0
    assert all(item["baseline"]["status"] == "passed" for item in report["cases"])
