"""Business-gold evaluation executed against a real MySQL instance.

Unlike the synthetic fixture evaluation, this module runs the gold SQL and the
model-generated SQL against the same live read-only database, so the reported
match rate reflects real execution rather than a versioned fixture.

The report never stores questions, SQL, parameter values or result rows: only
case IDs, statuses, stable error codes and timings.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from datetime import time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.agent.contracts import parse_sql_plan
from app.guardrails import validate_sql

Generate = Callable[..., Awaitable[Any]]
Execute = Callable[[str, dict[str, Any], int], tuple[list[str], list[list[Any]], bool]]

MAX_ROWS = 200
# Rounding precision depends on magnitude: money totals are conventionally
# rounded to 2 decimals, ratios to 4. A single absolute tolerance would either
# accept a genuinely wrong ratio (0.1988 vs 0.2037) or reject a correct money
# total, so the tolerance is banded by magnitude.
RATIO_TOLERANCE = Decimal("0.0001")
MONEY_TOLERANCE = Decimal("0.01")


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    return None


def _tolerance_for(magnitude: Decimal) -> Decimal:
    # Ratios and percentages live in [0, 1] and are conventionally printed with
    # four decimals; money, counts and averages are printed with two.
    return RATIO_TOLERANCE if abs(magnitude) <= 1 else MONEY_TOLERANCE


def _cells_equal(left: Any, right: Any) -> bool:
    left_decimal = _to_decimal(left)
    right_decimal = _to_decimal(right)
    if left_decimal is not None and right_decimal is not None:
        difference = abs(left_decimal - right_decimal)
        if difference <= _tolerance_for(max(abs(left_decimal), abs(right_decimal))):
            return True
        # Relative fallback protects very large totals from float noise.
        return left_decimal != 0 and difference <= abs(left_decimal) * Decimal("1e-9")
    return _normalize_value(left) == _normalize_value(right)


def _rows_equal(left: Sequence[Any], right: Sequence[Any]) -> bool:
    return len(left) == len(right) and all(_cells_equal(a, b) for a, b in zip(left, right))


def load_dataset(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("business gold dataset must contain a cases array")
    return payload


def validate_dataset(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > 200:
        raise ValueError("business gold dataset must contain 1 to 200 cases")
    identifiers: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("business gold cases must be objects")
        identifier = str(case.get("id") or "").strip()
        identifiers.append(identifier)
        if not identifier or not str(case.get("question") or "").strip() or not str(case.get("gold_sql") or "").strip():
            raise ValueError("business gold cases require id, question and gold_sql")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("business gold case IDs must be unique")
    return cases


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dt_time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float):
        return round(value, 10)
    return value


def normalize_rows(rows: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
    return [tuple(_normalize_value(cell) for cell in row) for row in rows]


def results_match(
    gold_rows: Sequence[Sequence[Any]],
    model_rows: Sequence[Sequence[Any]],
    *,
    ordered: bool,
) -> bool:
    """Compare values only, with a magnitude-aware numeric tolerance.

    Column labels are ignored so an equally correct query is not failed for
    choosing a different alias. Unordered comparisons match rows greedily
    because SQL without ORDER BY has no guaranteed row order.
    """
    if len(gold_rows) != len(model_rows):
        return False
    if not gold_rows:
        return True
    if any(len(row) != len(gold_rows[0]) for row in gold_rows):
        return False
    if any(len(row) != len(gold_rows[0]) for row in model_rows):
        return False
    if ordered:
        return all(_rows_equal(gold, model) for gold, model in zip(gold_rows, model_rows))
    remaining = [tuple(row) for row in model_rows]
    for gold_row in gold_rows:
        for index, model_row in enumerate(remaining):
            if _rows_equal(gold_row, model_row):
                remaining.pop(index)
                break
        else:
            return False
    return True


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 2)


def _rate(values: list[bool | None]) -> float | None:
    measured = [value for value in values if value is not None]
    return round(sum(measured) / len(measured), 4) if measured else None


def _is_transient(exc: Exception) -> bool:
    """Return True for network/5xx failures that should be retried.

    A malformed model answer or an auth error must not be retried: that would
    turn a real model failure into a silent success.
    """
    name = type(exc).__name__
    if any(marker in name for marker in ("Connect", "Timeout", "Protocol", "ReadError", "WriteError")):
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {429, 500, 502, 503, 504}


async def _generate_with_retry(generate: Generate, *, attempts: int, **kwargs: Any) -> Any:
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            return await generate(**kwargs)
        except Exception as exc:
            if attempt == attempts - 1 or not _is_transient(exc):
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")


async def _run_case(
    case: dict[str, Any],
    *,
    schema: dict[str, set[str]],
    execute: Execute,
    generate: Generate | None,
    generate_retries: int = 6,
) -> tuple[dict[str, Any], dict[str, bool | None]]:
    record: dict[str, Any] = {
        "id": str(case["id"]),
        "category": str(case.get("category") or "uncategorized"),
        "gold_status": "failed",
        "model_status": "not_run",
        "error_code": "",
        "duration_ms": 0,
    }
    flags: dict[str, bool | None] = {
        "gold": False,
        "generation": None,
        "guardrail": None,
        "execution": None,
        "match": None,
    }
    started = time.perf_counter()
    try:
        try:
            gold_sql = validate_sql(
                str(case["gold_sql"]),
                schema,
                max_length=16000,
                dialect="mysql",
                max_params=50,
            )
            _, gold_rows, gold_truncated = execute(gold_sql, dict(case.get("gold_params") or {}), MAX_ROWS)
            if gold_truncated or not gold_rows:
                raise ValueError("gold query returned no usable rows")
        except Exception:
            record["error_code"] = "E_GOLD_INTEGRITY"
            return record, flags
        flags["gold"] = True
        record["gold_status"] = "passed"
        record["gold_columns"] = len(gold_rows[0]) if gold_rows else 0

        if generate is None:
            record["error_code"] = "E_MODEL_NOT_CONFIGURED"
            return record, flags

        try:
            generated = await _generate_with_retry(
                generate,
                attempts=generate_retries,
                question=str(case["question"]),
                schema=schema,
                context={
                    "evaluation": "business_gold_mysql",
                    "dataset_version": str(case.get("_dataset_version") or "unknown"),
                },
                dialect="mysql",
            )
            plan = parse_sql_plan(generated)
        except Exception:
            flags["generation"] = False
            flags["match"] = False
            record.update(model_status="failed", error_code="E_MODEL_GENERATION")
            return record, flags
        flags["generation"] = True

        try:
            sql = validate_sql(plan.sql, schema, max_length=16000, dialect="mysql", max_params=50)
        except Exception as exc:
            flags["guardrail"] = False
            flags["match"] = False
            record.update(
                model_status="rejected",
                error_code=str(getattr(exc, "code", "") or "E_SQL_VALIDATION"),
            )
            return record, flags
        flags["guardrail"] = True

        try:
            _, model_rows, truncated = execute(sql, dict(plan.params), MAX_ROWS)
        except Exception:
            flags["execution"] = False
            flags["match"] = False
            record.update(model_status="failed", error_code="E_EXECUTION_ERROR")
            return record, flags
        flags["execution"] = True
        record["model_columns"] = len(model_rows[0]) if model_rows else 0

        matched = False
        if not truncated and len(model_rows) == len(gold_rows):
            matched = results_match(gold_rows, model_rows, ordered=bool(case.get("ordered")))
        flags["match"] = matched
        if matched:
            record["model_status"] = "passed"
        else:
            record["model_status"] = "result_mismatch"
            record["error_code"] = "E_EMPTY_RESULT" if not model_rows else "E_RESULT_MISMATCH"
        return record, flags
    finally:
        record["duration_ms"] = int((time.perf_counter() - started) * 1000)


async def evaluate_business_gold_mysql(
    *,
    dataset: dict[str, Any],
    schema: dict[str, set[str]],
    execute: Execute,
    generate: Generate | None,
    model: dict[str, Any] | None = None,
    generate_retries: int = 6,
) -> dict[str, Any]:
    cases = validate_dataset(dataset)
    dataset_version = str(dataset.get("dataset_version") or "unknown")
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
    latencies: list[float] = []

    for case in cases:
        prepared = {**case, "_dataset_version": dataset_version}
        record, flags = await _run_case(
            prepared, schema=schema, execute=execute, generate=generate, generate_retries=generate_retries
        )
        results.append(record)
        gold_integrity.append(bool(flags["gold"]))
        generation_success.append(flags["generation"])
        guardrail_pass.append(flags["guardrail"])
        execution_success.append(flags["execution"])
        result_match.append(flags["match"])
        if flags["execution"] is not None:
            latencies.append(float(record["duration_ms"]))

    local_passed = all(gold_integrity)
    generation_rate = _rate(generation_success)
    if generate is None:
        external_validation = "not_run"
        external_passed = False
        external_reasons = ["MODEL_NOT_CONFIGURED"]
    elif generation_rate is not None and generation_rate < 0.95:
        # A flaky model endpoint must not be reported as a model-quality result.
        external_validation = "inconclusive"
        external_passed = False
        external_reasons = ["MODEL_CONNECTIVITY_UNSTABLE"]
    else:
        external_passed = local_passed and all(item["model_status"] == "passed" for item in results)
        external_validation = "passed" if external_passed else "failed"
        external_reasons = [] if external_passed else ["MODEL_RESULT_REGRESSION"]
    attribution = Counter(item["error_code"] for item in results if item["error_code"])
    return {
        "format": "query-pilot-business-gold-mysql-report",
        "version": 1,
        "dataset_version": dataset_version,
        "database": "mysql-enterprise-demo",
        "model": safe_model,
        "comparison": {
            "column_labels": "ignored",
            "row_order": "enforced only when the case declares ordered=true",
            "numeric_tolerance": {
                "abs_le_1": str(RATIO_TOLERANCE),
                "abs_gt_1": str(MONEY_TOLERANCE),
            },
        },
        "external_validation": external_validation,
        "metrics": {
            "cases": len(cases),
            "gold_integrity_rate": _rate(gold_integrity),
            "generation_success_rate": generation_rate,
            "generation_retries": generate_retries,
            "guardrail_pass_rate": _rate(guardrail_pass),
            "execution_success_rate": _rate(execution_success),
            # End-to-end: generation, guardrail and execution failures all count
            # as a miss, so this number cannot be inflated by rejecting SQL.
            "result_exact_match_rate": _rate(result_match),
            "latency_p50_ms": _percentile(latencies, 0.5),
            "latency_p95_ms": _percentile(latencies, 0.95),
        },
        "attribution": dict(sorted(attribution.items())),
        "local_gate": {
            "passed": local_passed,
            "reason_codes": [] if local_passed else ["GOLD_INTEGRITY_FAILURE"],
        },
        "external_gate": {"passed": external_passed, "reason_codes": external_reasons},
        "cases": results,
        "limitations": [
            "The database holds synthetic enterprise demo data, not real enterprise business data.",
            "Matching covers only the versioned questions in this dataset.",
            "The report does not store questions, generated SQL, parameter values or result rows.",
        ],
    }
