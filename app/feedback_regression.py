"""Fail-closed regression runner for administrator-approved query feedback.

The runner intentionally reports only stable codes and aggregate evidence. It
never persists generated SQL, parameter values, result rows, provider payloads,
or exception text.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

import sqlglot
from sqlglot import exp

from app.agent.contracts import parse_sql_plan
from app.guardrails import QueryError, validate_sql


Generate = Callable[..., Awaitable[Any]]
SchemaFor = Callable[[dict[str, Any]], dict[str, Iterable[str]] | Awaitable[dict[str, Iterable[str]]]]
DialectFor = Callable[[dict[str, Any]], str]
Execute = Callable[
    [dict[str, Any], str, dict[str, Any]],
    tuple[list[str], list[list[Any]], bool] | Awaitable[tuple[list[str], list[list[Any]], bool]],
]


def _stable_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _stable_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    return str(value)


def _digest(payload: Any) -> str:
    encoded = json.dumps(_stable_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _expression_columns(expression: exp.Expression | None) -> list[str]:
    if expression is None:
        return []
    return sorted({column.name.lower() for column in expression.find_all(exp.Column) if column.name})


def build_sql_contract(sql: str, *, dialect: str = "sqlite") -> dict[str, Any]:
    """Return a literal-free structural contract for one SELECT statement."""
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except Exception as exc:
        raise ValueError("E_GOLD_SQL_INVALID") from exc
    if not isinstance(statement, exp.Select):
        raise ValueError("E_GOLD_SQL_INVALID")

    joins = list(statement.find_all(exp.Join))
    group = statement.args.get("group")
    order = statement.args.get("order")
    predicates = []
    for clause_name in ("where", "having", "qualify"):
        clause = statement.args.get(clause_name)
        if clause is None:
            continue
        predicates.extend(
            node.key.upper()
            for node in clause.walk()
            if isinstance(node, (exp.Predicate, exp.Connector))
        )
    aggregates = sorted(
        function.sql_name().upper()
        for function in statement.find_all(exp.AggFunc)
        if function.sql_name()
    )
    tables = sorted({table.name.lower() for table in statement.find_all(exp.Table) if table.name})
    columns = sorted({column.name.lower() for column in statement.find_all(exp.Column) if column.name})
    return {
        "tables": tables,
        "columns": columns,
        "aggregates": aggregates,
        "group_by_columns": _expression_columns(group),
        "order_by_columns": _expression_columns(order),
        "predicate_operators": sorted(predicates),
        "projection_count": len(statement.expressions),
        "join_count": len(joins),
        "join_types": sorted(str(join.args.get("side") or join.args.get("kind") or "INNER").upper() for join in joins),
        "cte_count": len(list(statement.find_all(exp.CTE))),
        "distinct": statement.args.get("distinct") is not None,
    }


def build_result_contract(
    columns: Iterable[str],
    rows: Iterable[Iterable[Any]],
    *,
    truncated: bool,
    order_sensitive: bool = True,
) -> dict[str, Any]:
    """Build a result digest without retaining result values."""
    normalized_columns = [str(column) for column in columns]
    normalized_rows = [[_stable_value(cell) for cell in row] for row in rows]
    if not order_sensitive:
        normalized_rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return {
        "columns": normalized_columns,
        "row_count": len(normalized_rows),
        "truncated": bool(truncated),
        "order_sensitive": bool(order_sensitive),
        "verification_eligible": not truncated,
        "rows_sha256": _digest(normalized_rows),
    }


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _rate(values: list[bool | None]) -> float | None:
    measured = [value for value in values if value is not None]
    return (sum(measured) / len(measured)) if measured else None


def _case_result(feedback_id: str) -> dict[str, Any]:
    return {
        "feedback_id": feedback_id,
        "status": "failed",
        "error_code": "",
        "structure_match": None,
        "result_match": None,
    }


async def evaluate_feedback_regression(
    *,
    dataset: dict[str, Any],
    generate: Generate | None,
    schema_for: SchemaFor,
    dialect_for: DialectFor,
    execute: Execute | None = None,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay approved feedback against the current model and safety boundary."""
    all_cases = dataset.get("cases", []) if isinstance(dataset, dict) else []
    all_cases = all_cases if isinstance(all_cases, list) else []
    approved = [case for case in all_cases if isinstance(case, dict) and case.get("review_status") == "approved"]
    ignored = len(all_cases) - len(approved)
    safe_model = {
        "provider": str((model or {}).get("provider") or "unknown")[:128],
        "model": str((model or {}).get("model") or "unknown")[:160],
        "prompt_version": str((model or {}).get("prompt_version") or "unknown")[:128],
    }
    created_at = datetime.now(timezone.utc).isoformat()

    if generate is None:
        results = []
        for case in approved:
            item = _case_result(str(case.get("feedback_id") or ""))
            item.update(status="not_run", error_code="E_MODEL_NOT_CONFIGURED")
            results.append(item)
        return {
            "format": "query-pilot-feedback-regression-report",
            "version": 1,
            "created_at_utc": created_at,
            "dataset_version": str(dataset.get("dataset_version") or ""),
            "synthetic": bool(dataset.get("synthetic")),
            "external_validation": "not_run",
            "model": safe_model,
            "metrics": {
                "approved_cases": len(approved),
                "ignored_unapproved_cases": ignored,
                "passed": 0,
                "failed": 0,
                "not_run": len(approved),
                "generation_success_rate": None,
                "guardrail_pass_rate": None,
                "structure_match_rate": None,
                "result_match_rate": None,
            },
            "gate": {"passed": False, "reason_codes": ["MODEL_NOT_CONFIGURED"]},
            "cases": results,
            "limitations": _limitations(),
        }

    results: list[dict[str, Any]] = []
    generation_success: list[bool | None] = []
    guardrail_passed: list[bool | None] = []
    structure_matches: list[bool | None] = []
    result_matches: list[bool | None] = []
    for case in approved:
        item = _case_result(str(case.get("feedback_id") or ""))
        results.append(item)
        expected_template = str(case.get("expected_sql_template") or "").strip()
        if not expected_template:
            item["error_code"] = "E_GOLD_SQL_MISSING"
            generation_success.append(None)
            guardrail_passed.append(None)
            structure_matches.append(None)
            result_matches.append(None)
            continue
        try:
            expected_contract = case.get("expected_sql_contract") or build_sql_contract(
                expected_template,
                dialect=str(dialect_for(case) or "sqlite"),
            )
        except Exception:
            item["error_code"] = "E_GOLD_SQL_INVALID"
            generation_success.append(None)
            guardrail_passed.append(None)
            structure_matches.append(None)
            result_matches.append(None)
            continue

        dialect = str(dialect_for(case) or "sqlite")
        try:
            schema = await _resolve(schema_for(case))
            schema = {str(table): set(columns) for table, columns in schema.items()}
        except Exception:
            item["error_code"] = "E_SCHEMA_UNAVAILABLE"
            generation_success.append(None)
            guardrail_passed.append(None)
            structure_matches.append(None)
            result_matches.append(None)
            continue
        try:
            generated = await generate(
                question=str(case.get("question") or ""),
                schema=schema,
                context={
                    "evaluation": "approved_feedback_regression",
                    "dataset_version": str(dataset.get("dataset_version") or ""),
                },
                dialect=dialect,
            )
            plan = parse_sql_plan(generated)
            generation_success.append(True)
        except Exception:
            item["error_code"] = "E_MODEL_GENERATION"
            generation_success.append(False)
            guardrail_passed.append(None)
            structure_matches.append(None)
            result_matches.append(None)
            continue

        try:
            validated = validate_sql(plan.sql, schema, max_length=16000, dialect=dialect, max_params=50)
            guardrail_passed.append(True)
        except QueryError as exc:
            item.update(status="rejected", error_code=exc.code)
            guardrail_passed.append(False)
            structure_matches.append(None)
            result_matches.append(None)
            continue
        except Exception:
            item["error_code"] = "E_SQL_VALIDATION"
            guardrail_passed.append(False)
            structure_matches.append(None)
            result_matches.append(None)
            continue

        try:
            actual_contract = build_sql_contract(validated, dialect=dialect)
        except ValueError:
            item["error_code"] = "E_SQL_VALIDATION"
            structure_matches.append(False)
            result_matches.append(None)
            continue
        structure_match = actual_contract == expected_contract
        item["structure_match"] = structure_match
        structure_matches.append(structure_match)
        if not structure_match:
            item["error_code"] = "E_SQL_STRUCTURE_MISMATCH"
            result_matches.append(None)
            continue

        expected_result = case.get("expected_result_contract")
        if (
            isinstance(expected_result, dict)
            and expected_result.get("verification_eligible") is True
            and expected_result.get("rows_sha256")
            and execute is not None
        ):
            try:
                columns, rows, truncated = await _resolve(execute(case, validated, dict(plan.params)))
                actual_result = build_result_contract(
                    columns,
                    rows,
                    truncated=truncated,
                    order_sensitive=bool(expected_result.get("order_sensitive", True)),
                )
                result_match = (
                    actual_result["columns"] == expected_result.get("columns")
                    and actual_result["row_count"] == expected_result.get("row_count")
                    and actual_result["truncated"] == expected_result.get("truncated")
                    and actual_result["rows_sha256"] == expected_result.get("rows_sha256")
                )
            except Exception:
                item["error_code"] = "E_EXECUTION_ERROR"
                result_matches.append(False)
                continue
            item["result_match"] = result_match
            result_matches.append(result_match)
            if not result_match:
                item["error_code"] = "E_RESULT_MISMATCH"
                continue
        else:
            result_matches.append(None)
        item["status"] = "passed"

    failed = sum(item["status"] != "passed" for item in results)
    reason_codes: list[str] = []
    if not approved:
        reason_codes.append("DATASET_EMPTY")
    if failed:
        reason_codes.append("CASE_FAILURES")
    return {
        "format": "query-pilot-feedback-regression-report",
        "version": 1,
        "created_at_utc": created_at,
        "dataset_version": str(dataset.get("dataset_version") or ""),
        "synthetic": bool(dataset.get("synthetic")),
        "external_validation": "passed" if approved and not failed else "failed",
        "model": safe_model,
        "metrics": {
            "approved_cases": len(approved),
            "ignored_unapproved_cases": ignored,
            "passed": len(results) - failed,
            "failed": failed,
            "not_run": 0,
            "generation_success_rate": _rate(generation_success),
            "guardrail_pass_rate": _rate(guardrail_passed),
            "structure_match_rate": _rate(structure_matches),
            "result_match_rate": _rate(result_matches),
        },
        "gate": {"passed": not reason_codes, "reason_codes": reason_codes},
        "cases": results,
        "limitations": _limitations(),
    }


def _limitations() -> list[str]:
    return [
        "Approved feedback is administrator-reviewed evidence, not automatically trusted enterprise ground truth.",
        "Structure-only cases do not prove business-result correctness; result comparison runs only when a reproducible digest exists.",
        "The report never stores generated SQL, parameter values, result rows, provider payloads, or exception text.",
    ]


__all__ = ["build_result_contract", "build_sql_contract", "evaluate_feedback_regression"]
