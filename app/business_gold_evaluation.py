"""Versioned synthetic Question/SQL/Result evaluation for Query Pilot."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.agent.contracts import parse_sql_plan
from app.guardrails import QueryError, validate_sql
from app.skill_query_evaluation import SKILL_EVAL_SCHEMA, _create_fixture, _execute_fixture_readonly


Generate = Callable[..., Awaitable[Any]]


def _rate(values: list[bool | None]) -> float | None:
    measured = [value for value in values if value is not None]
    return sum(measured) / len(measured) if measured else None


def load_business_gold_dataset(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("business gold dataset must contain a cases array")
    return payload


def _validate_dataset(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > 100:
        raise ValueError("business gold dataset must contain 1 to 100 cases")
    identifiers = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("business gold cases must be objects")
        identifier = str(case.get("id") or "").strip()
        identifiers.append(identifier)
        if not identifier or not str(case.get("question") or "").strip() or not str(case.get("gold_sql") or "").strip():
            raise ValueError("business gold cases require id, question and gold_sql")
        if not isinstance(case.get("expected_columns"), list) or not isinstance(case.get("expected_rows"), list):
            raise ValueError("business gold cases require expected_columns and expected_rows")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("business gold case IDs must be unique")
    return cases


async def evaluate_business_gold(
    *,
    dataset: dict[str, Any],
    generate: Generate | None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cases = _validate_dataset(dataset)
    safe_model = {
        "provider": str((model or {}).get("provider") or "unknown")[:128],
        "model": str((model or {}).get("model") or "unknown")[:160],
        "prompt_version": str((model or {}).get("prompt_version") or "unknown")[:128],
    }
    results: list[dict[str, Any]] = []
    gold_integrity: list[bool] = []
    generation_success: list[bool | None] = []
    guardrail_pass: list[bool | None] = []
    execution_success: list[bool | None] = []
    result_match: list[bool | None] = []

    with tempfile.TemporaryDirectory(prefix="querypilot-business-gold-") as directory:
        database_path = str(Path(directory) / "fixture.sqlite")
        _create_fixture(database_path)
        for case in cases:
            item = {
                "id": str(case["id"]),
                "category": str(case.get("category") or "uncategorized"),
                "gold_status": "failed",
                "model_status": "not_run",
                "error_code": "",
            }
            results.append(item)
            try:
                gold_sql = validate_sql(
                    str(case["gold_sql"]),
                    SKILL_EVAL_SCHEMA,
                    max_length=16000,
                    dialect="sqlite",
                    max_params=50,
                )
                gold_columns, gold_rows, gold_truncated = _execute_fixture_readonly(
                    database_path,
                    gold_sql,
                    dict(case.get("gold_params") or {}),
                )
                gold_ok = (
                    not gold_truncated
                    and gold_columns == case["expected_columns"]
                    and gold_rows == case["expected_rows"]
                )
            except Exception:
                gold_ok = False
            gold_integrity.append(gold_ok)
            item["gold_status"] = "passed" if gold_ok else "failed"
            if not gold_ok:
                item["error_code"] = "E_GOLD_INTEGRITY"
                generation_success.append(None)
                guardrail_pass.append(None)
                execution_success.append(None)
                result_match.append(None)
                continue
            if generate is None:
                item["error_code"] = "E_MODEL_NOT_CONFIGURED"
                generation_success.append(None)
                guardrail_pass.append(None)
                execution_success.append(None)
                result_match.append(None)
                continue
            try:
                generated = await generate(
                    question=str(case["question"]),
                    schema=SKILL_EVAL_SCHEMA,
                    context={
                        "evaluation": "business_gold",
                        "dataset_version": str(dataset.get("dataset_version") or dataset.get("version") or "unknown"),
                    },
                    dialect="sqlite",
                )
                plan = parse_sql_plan(generated)
                generation_success.append(True)
            except Exception:
                item.update(model_status="failed", error_code="E_MODEL_GENERATION")
                generation_success.append(False)
                guardrail_pass.append(None)
                execution_success.append(None)
                result_match.append(None)
                continue
            try:
                sql = validate_sql(plan.sql, SKILL_EVAL_SCHEMA, max_length=16000, dialect="sqlite", max_params=50)
                guardrail_pass.append(True)
            except QueryError as exc:
                item.update(model_status="rejected", error_code=exc.code)
                guardrail_pass.append(False)
                execution_success.append(None)
                result_match.append(None)
                continue
            except Exception:
                item.update(model_status="failed", error_code="E_SQL_VALIDATION")
                guardrail_pass.append(False)
                execution_success.append(None)
                result_match.append(None)
                continue
            try:
                columns, rows, truncated = _execute_fixture_readonly(database_path, sql, dict(plan.params))
                execution_success.append(True)
            except Exception:
                item.update(model_status="failed", error_code="E_EXECUTION_ERROR")
                execution_success.append(False)
                result_match.append(False)
                continue
            matched = not truncated and columns == case["expected_columns"] and rows == case["expected_rows"]
            result_match.append(matched)
            item["model_status"] = "passed" if matched else "result_mismatch"
            if not matched:
                item["error_code"] = "E_RESULT_MISMATCH"

    local_passed = all(gold_integrity)
    if generate is None:
        external_validation = "not_run"
        external_passed = False
        external_reasons = ["MODEL_NOT_CONFIGURED"]
    else:
        external_passed = local_passed and all(item["model_status"] == "passed" for item in results)
        external_validation = "passed" if external_passed else "failed"
        external_reasons = [] if external_passed else ["MODEL_RESULT_REGRESSION"]
    return {
        "format": "query-pilot-business-gold-report",
        "version": 1,
        "dataset_version": str(dataset.get("dataset_version") or dataset.get("version") or "unknown"),
        "synthetic": bool(dataset.get("synthetic", True)),
        "database": "isolated_synthetic_sqlite_fixture",
        "model": safe_model,
        "external_validation": external_validation,
        "metrics": {
            "cases": len(cases),
            "gold_integrity_rate": _rate(gold_integrity),
            "generation_success_rate": _rate(generation_success),
            "guardrail_pass_rate": _rate(guardrail_pass),
            "execution_success_rate": _rate(execution_success),
            "result_exact_match_rate": _rate(result_match),
        },
        "local_gate": {
            "passed": local_passed,
            "reason_codes": [] if local_passed else ["GOLD_INTEGRITY_FAILURE"],
        },
        "external_gate": {"passed": external_passed, "reason_codes": external_reasons},
        "cases": results,
        "limitations": [
            "The dataset is synthetic and does not establish enterprise business accuracy.",
            "Exact result matching covers only the versioned questions, SQL semantics and fixture rows in this dataset.",
            "The report does not store generated SQL, model payloads, parameter values or result rows.",
        ],
    }


__all__ = ["evaluate_business_gold", "load_business_gold_dataset"]
