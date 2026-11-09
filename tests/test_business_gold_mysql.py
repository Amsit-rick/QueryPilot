from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.business_gold_mysql_evaluation import (
    _generate_with_retry,
    evaluate_business_gold_mysql,
    load_dataset,
    results_match,
)
from app.guardrails import validate_sql

SCHEMA = {"sales": {"region", "amount", "sold_at"}, "customers": {"id", "name", "region"}}


def test_results_match_ignores_column_labels_and_row_order():
    gold = [["华东", Decimal("100.00")], ["华南", Decimal("20.00")]]
    renamed = [["region", "amount"], ["华南", Decimal("20")], ["华东", Decimal("100")]]

    assert results_match(gold, renamed[1:], ordered=False) is True
    assert results_match(gold, renamed[1:], ordered=True) is False


def test_numeric_tolerance_accepts_rounding_but_not_a_real_difference():
    assert results_match(
        [["direct", Decimal("124822.60")]],
        [["direct", Decimal("124822.595446")]],
        ordered=False,
    )
    assert not results_match(
        [["华东", Decimal("0.1988")]],
        [["华东", Decimal("0.2037")]],
        ordered=False,
    )


def test_results_match_detects_value_and_shape_differences():
    gold = [["华东", Decimal("100")]]
    assert results_match(gold, [["华东", Decimal("101")]], ordered=False) is False
    assert results_match(gold, [["华东"]], ordered=False) is False
    assert results_match(gold, [["华东", Decimal("100")], ["华南", Decimal("1")]], ordered=False) is False


def _dataset():
    return {
        "dataset_version": "test-v1",
        "cases": [
            {"id": "T-1", "category": "ok", "question": "列出区域", "gold_sql": "SELECT region FROM sales ORDER BY region", "ordered": True},
            {"id": "T-2", "category": "hallucination", "question": "查密码", "gold_sql": "SELECT region FROM sales ORDER BY region"},
            {"id": "T-3", "category": "execution", "question": "华东区域", "gold_sql": "SELECT region FROM sales WHERE region = '华东'"},
            {"id": "T-4", "category": "mismatch", "question": "倒序列出区域", "gold_sql": "SELECT region FROM sales ORDER BY region", "ordered": True},
        ],
    }


async def _generate(**kwargs):
    return {
        "列出区域": "SELECT region FROM sales ORDER BY region",
        "查密码": "SELECT password_hash FROM sales",
        "华东区域": "SELECT region FROM sales",
        "倒序列出区域": "SELECT region FROM sales ORDER BY region DESC",
    }[kwargs["question"]]


def _execute(sql, params, max_rows):
    if "FROM sales" in sql and "WHERE" not in sql and "DESC" not in sql and "ORDER BY region" not in sql:
        raise RuntimeError("database unavailable")
    if "ORDER BY region DESC" in sql:
        return ["region"], [["华南"], ["华东"]], False
    return ["region"], [["华东"], ["华南"]], False


def test_report_attributes_each_failure_mode_and_counts_them():
    report = asyncio.run(evaluate_business_gold_mysql(
        dataset=_dataset(),
        schema=SCHEMA,
        execute=_execute,
        generate=_generate,
        model={"provider": "test", "model": "fake", "prompt_version": "p1"},
    ))

    assert report["metrics"]["cases"] == 4
    assert report["metrics"]["gold_integrity_rate"] == 1.0
    assert report["metrics"]["result_exact_match_rate"] == 0.25
    statuses = {item["id"]: (item["model_status"], item["error_code"]) for item in report["cases"]}
    assert statuses["T-1"] == ("passed", "")
    assert statuses["T-2"] == ("rejected", "E_HALLUCINATED_FIELD")
    assert statuses["T-3"] == ("failed", "E_EXECUTION_ERROR")
    assert statuses["T-4"] == ("result_mismatch", "E_RESULT_MISMATCH")
    assert report["attribution"]["E_HALLUCINATED_FIELD"] == 1
    assert report["external_gate"]["passed"] is False


def test_report_does_not_store_questions_sql_or_result_rows():
    report = asyncio.run(evaluate_business_gold_mysql(
        dataset=_dataset(),
        schema=SCHEMA,
        execute=_execute,
        generate=_generate,
        model={"provider": "test", "model": "fake", "prompt_version": "p1"},
    ))
    serialized = json.dumps(report, ensure_ascii=False)

    assert "列出区域" not in serialized
    assert "SELECT region" not in serialized
    assert "华东" not in serialized
    assert all("question" not in item and "sql" not in item for item in report["cases"])


def test_without_model_the_report_is_explicitly_not_run():
    report = asyncio.run(evaluate_business_gold_mysql(
        dataset=_dataset(),
        schema=SCHEMA,
        execute=_execute,
        generate=None,
    ))

    assert report["external_validation"] == "not_run"
    assert report["metrics"]["result_exact_match_rate"] is None
    assert report["local_gate"]["passed"] is True


def test_versioned_dataset_gold_sql_is_accepted_by_the_guardrails():
    dataset = load_dataset(Path(__file__).resolve().parents[1] / "data" / "business_gold_mysql.json")
    assert len(dataset["cases"]) == 40
    schema = {
        "enterprise_orders": {"id", "order_no", "customer_id", "order_date", "region", "status", "channel", "total_amount"},
        "enterprise_customers": {"id", "customer_code", "name", "region", "industry", "level", "signup_date", "credit_limit"},
        "order_items": {"id", "order_id", "product_id", "quantity", "unit_price", "discount", "line_amount"},
        "payments": {"id", "payment_no", "order_id", "paid_date", "amount", "method", "status"},
        "inventory": {"id", "product_id", "warehouse", "available_qty", "reorder_point", "snapshot_date"},
        "products": {"id", "sku", "name", "category", "cost", "list_price"},
        "suppliers": {"id", "supplier_code", "name", "region", "rating", "active"},
        "employees": {"id", "employee_no", "department", "region", "hire_date", "monthly_cost"},
    }
    for case in dataset["cases"]:
        validate_sql(case["gold_sql"], schema, dialect="mysql")


class ConnectError(Exception):
    pass


def test_transient_generation_error_is_retried_then_succeeds():
    calls = []

    async def generate(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectError("network blip")
        return {"sql": "SELECT region FROM sales", "params": {}}

    result = asyncio.run(_generate_with_retry(generate, attempts=3, question="q"))
    assert result["sql"] == "SELECT region FROM sales"
    assert len(calls) == 2


def test_non_transient_generation_error_is_not_retried():
    calls = []

    async def generate(**_kwargs):
        calls.append(1)
        raise ValueError("model returned garbage")

    with pytest.raises(ValueError):
        asyncio.run(_generate_with_retry(generate, attempts=3, question="q"))
    assert len(calls) == 1


def test_retries_are_bounded():
    calls = []

    async def generate(**_kwargs):
        calls.append(1)
        raise ConnectError("still down")

    with pytest.raises(ConnectError):
        asyncio.run(_generate_with_retry(generate, attempts=2, question="q"))
    assert len(calls) == 2


def test_unreliable_model_endpoint_is_reported_as_inconclusive():
    async def always_fails(**_kwargs):
        raise ValueError("model unavailable")

    report = asyncio.run(evaluate_business_gold_mysql(
        dataset=_dataset(),
        schema=SCHEMA,
        execute=_execute,
        generate=always_fails,
        model={"provider": "test", "model": "fake", "prompt_version": "p1"},
    ))

    assert report["metrics"]["generation_success_rate"] == 0.0
    assert report["external_validation"] == "inconclusive"
    assert report["external_gate"]["reason_codes"] == ["MODEL_CONNECTIVITY_UNSTABLE"]
    # The accuracy number is still computed but must not be read as model quality.
    assert report["metrics"]["result_exact_match_rate"] == 0.0
