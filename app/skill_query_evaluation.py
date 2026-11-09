"""Compare Skill-aware SQL generation using a disposable synthetic SQLite fixture."""

from __future__ import annotations

import tempfile
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.agent.contracts import parse_sql_plan
from app.guardrails import QueryError, validate_sql
from app.repositories.memory_skill_repository import InMemorySkillRepository
from app.services.skill_service import SkillService


Generate = Callable[..., Awaitable[Any]]

SKILL_EVAL_SCHEMA = {
    "sales": {"id", "region", "amount", "sold_at"},
    "customers": {"id", "name", "region"},
    "orders": {"id", "customer_id", "status", "order_date"},
    "order_items": {"id", "order_id", "product_id", "quantity", "unit_price"},
    "products": {"id", "name", "category"},
    "payments": {"id", "order_id", "amount", "paid_at"},
}


def _service(catalog: list[dict[str, Any]]) -> SkillService:
    from app.domain.skill import SkillDefinition

    definitions = tuple(SkillDefinition.model_validate(item) for item in catalog)
    return SkillService(skills=definitions, repository=InMemorySkillRepository())


def _skill_context(route: dict[str, Any]) -> dict[str, Any]:
    skill = route.get("selected_skill")
    if not skill:
        return {}
    deployment = skill.get("deployment", {})
    return {
        "skill": {
            "id": skill["id"],
            "name": skill["name"],
            "version": skill["version"],
            "state": deployment.get("state", "builtin"),
            "description": skill["description"],
            "intents": skill["intents"],
            "workflow_steps": skill["workflow_steps"],
            "matched_terms": route["evidence"]["matched_terms"],
        }
    }


def _rate(values: list[bool | None]) -> float | None:
    measured = [value for value in values if value is not None]
    return sum(measured) / len(measured) if measured else None


def _metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "cases": len(results),
        "generation_success_rate": _rate([item["model_generation_success"] for item in results]),
        "sql_valid_rate": _rate([item["sql_valid"] for item in results]),
        "guardrail_pass_rate": _rate([item["guardrail_passed"] for item in results]),
        "execution_success_rate": _rate([item["execution_success"] for item in results]),
        "result_exact_match_rate": _rate([item["business_correct"] for item in results]),
        "passed": sum(item["status"] == "passed" for item in results),
    }


def _execute_fixture_readonly(
    db_path: str,
    sql: str,
    params: dict[str, Any],
    *,
    max_rows: int = 100,
) -> tuple[list[str], list[list[Any]], bool]:
    connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 1000)
        cursor = connection.execute(sql, params)
        columns = [column[0] for column in cursor.description or []]
        rows = cursor.fetchmany(max_rows + 1)
        return columns, [list(row) for row in rows[:max_rows]], len(rows) > max_rows
    finally:
        connection.close()


def _create_fixture(db_path: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript("""
            CREATE TABLE sales(id INTEGER PRIMARY KEY, region TEXT NOT NULL, amount REAL NOT NULL, sold_at TEXT NOT NULL);
            CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
            CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, status TEXT NOT NULL, order_date TEXT NOT NULL);
            CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL);
            CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL);
            CREATE TABLE payments(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, amount REAL NOT NULL, paid_at TEXT NOT NULL);
            INSERT INTO sales VALUES
              (1, '华东', 1200.0, '2026-08-01'),
              (2, '华南', 980.0, '2026-08-02'),
              (3, '华东', 760.0, '2026-08-03');
            INSERT INTO customers VALUES
              (1, '星河科技', '华东'), (2, '南方零售', '华南'),
              (3, '北辰制造', '华东'), (4, '海风餐饮', '华南'), (5, '新月商贸', '华南');
            INSERT INTO orders VALUES
              (101, 1, 'paid', '2026-08-01'), (102, 1, 'paid', '2026-08-02'),
              (103, 2, 'paid', '2026-08-03'), (104, 2, 'cancelled', '2026-08-04'),
              (105, 3, 'paid', '2026-07-31'), (106, 3, 'pending', '2026-08-05'),
              (107, 4, 'paid', '2026-08-06');
            INSERT INTO products VALUES
              (1, '设备A', 'Hardware'), (2, '配件B', 'Hardware'),
              (3, '服务C', 'Consulting'), (4, '餐品D', 'Dining');
            INSERT INTO order_items VALUES
              (1, 101, 1, 2, 100.0), (2, 101, 2, 1, 50.0),
              (3, 102, 1, 1, 100.0), (4, 103, 3, 3, 40.0),
              (5, 104, 2, 2, 50.0), (6, 105, 3, 5, 40.0),
              (7, 106, 1, 4, 100.0), (8, 107, 4, 1, 80.0);
            INSERT INTO payments VALUES
              (1, 101, 180.0, '2026-08-01'), (2, 101, 70.0, '2026-08-01'),
              (3, 102, 100.0, '2026-08-02'), (4, 103, 120.0, '2026-08-03'),
              (5, 104, 100.0, '2026-08-04'), (6, 105, 200.0, '2026-07-31'),
              (7, 107, 80.0, '2026-08-06');
        """)
        connection.commit()
    finally:
        connection.close()


async def _evaluate_case(
    *,
    case: dict[str, Any],
    service: SkillService,
    generate: Generate,
    db_path: str,
) -> dict[str, Any]:
    question = case["question"]
    route = service.route(question)
    result: dict[str, Any] = {
        "id": case["id"],
        "category": case.get("category", "uncategorized"),
        "skill_id": route["selected_skill"]["id"] if route["selected_skill"] else None,
        "skill_version": route["selected_skill"]["version"] if route["selected_skill"] else None,
        "matched_terms": route["evidence"]["matched_terms"],
        "sql_valid": None,
        "guardrail_passed": None,
        "execution_success": None,
        "business_correct": None,
        "status": "failed",
    }
    try:
        generated = await generate(
            question=question,
            schema=SKILL_EVAL_SCHEMA,
            context=_skill_context(route),
            dialect="sqlite",
        )
        plan = parse_sql_plan(generated)
    except Exception:
        result.update({"model_generation_success": False, "error_code": "E_MODEL_GENERATION"})
        return result
    result["model_generation_success"] = True

    try:
        sql = validate_sql(
            plan.sql,
            SKILL_EVAL_SCHEMA,
            max_length=16000,
            dialect="sqlite",
            max_params=50,
        )
        result.update({"sql_valid": True, "guardrail_passed": True})
    except QueryError as exc:
        result.update({"sql_valid": False, "guardrail_passed": False, "error_code": exc.code, "status": "rejected"})
        return result

    try:
        columns, rows, truncated = _execute_fixture_readonly(db_path, sql, plan.params)
        result["execution_success"] = True
    except Exception as exc:
        result.update({
            "execution_success": False,
            "business_correct": False,
            "error_code": str(exc).partition(":")[0] if str(exc).startswith("E_") else "E_EXECUTION_ERROR",
        })
        return result

    result["business_correct"] = (
        not truncated
        and columns == case["expected_columns"]
        and rows == case["expected_rows"]
    )
    result["row_count"] = len(rows)
    result["status"] = "passed" if result["business_correct"] else "result_mismatch"
    return result


async def compare_skill_query_quality(
    *,
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    generate: Generate,
    dataset_version: str = "adhoc",
) -> dict[str, Any]:
    """Run both catalogs against a bounded synthetic DB; never touch app data."""
    ids = [str(case.get("id", "")).strip() for case in cases]
    if not cases or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("evaluation cases must have unique non-empty IDs")
    if len(cases) > 20:
        raise ValueError("query quality evaluation is limited to 20 cases per run")
    for case in cases:
        if not str(case.get("question", "")).strip() or not case.get("gold_sql"):
            raise ValueError("each evaluation case requires a question and gold_sql")
        if not isinstance(case.get("expected_columns"), list) or not isinstance(case.get("expected_rows"), list):
            raise ValueError("each evaluation case requires expected columns and rows")

    baseline_service = _service(baseline)
    candidate_service = _service(candidate)
    with tempfile.TemporaryDirectory(prefix="querypilot-skill-eval-") as directory:
        db_path = str(Path(directory) / "fixture.sqlite")
        _create_fixture(db_path)
        for case in cases:
            gold_sql = validate_sql(case["gold_sql"], SKILL_EVAL_SCHEMA, dialect="sqlite")
            columns, rows, truncated = _execute_fixture_readonly(db_path, gold_sql, {})
            if truncated or columns != case["expected_columns"] or rows != case["expected_rows"]:
                raise ValueError(f"gold fixture mismatch: {case['id']}")

        comparisons = []
        baseline_results = []
        candidate_results = []
        for case in cases:
            old = await _evaluate_case(case=case, service=baseline_service, generate=generate, db_path=db_path)
            new = await _evaluate_case(case=case, service=candidate_service, generate=generate, db_path=db_path)
            baseline_results.append(old)
            candidate_results.append(new)
            comparisons.append({"id": case["id"], "baseline": old, "candidate": new})

    baseline_metrics = _metrics(baseline_results)
    candidate_metrics = _metrics(candidate_results)
    return {
        "evaluation_type": "skill_sql_result_quality",
        "dataset_version": dataset_version,
        "generation_attempts": len(cases) * 2,
        "database": "isolated_synthetic_sqlite",
        "summary": {"cases": len(cases), "baseline": baseline_metrics, "candidate": candidate_metrics},
        "cases": comparisons,
        "limitations": [
            "固定合成数据只衡量当前用例的 SQL 执行与结果匹配。",
            "结果匹配不证明指标定义适用于真实业务。",
            "Skill 工作步骤仅以上下文交给模型，不是确定性工作流节点。",
        ],
    }


def load_skill_query_cases(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(payload.get("version", "unknown")), payload.get("cases", [])


__all__ = ["compare_skill_query_quality", "load_skill_query_cases"]
