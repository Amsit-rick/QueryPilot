"""Evaluate the 40-case business gold dataset against the real MySQL instance.

Gold SQL and model SQL both execute on the same live read-only database. The
JSON report contains only case IDs, statuses, stable error codes and timings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent import build_sql_generator  # noqa: E402
from app.agent.langchain_adapter import generate_sql as generate_sql_langchain  # noqa: E402
from app.business_gold_mysql_evaluation import (  # noqa: E402
    evaluate_business_gold_mysql,
    load_dataset,
    validate_dataset,
)
from app.config import settings  # noqa: E402
from app.guardrails import validate_sql  # noqa: E402
from app.llm import generate_sql  # noqa: E402
from app.model_defaults import DEFAULT_PROMPT_VERSION, compose_system_prompt  # noqa: E402
from app.mysql_db import _compile_mysql, mysql_schema, query_connection  # noqa: E402
from app.services.agent_query_service import AgentQueryService  # noqa: E402


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def restricted_schema(allowed_tables: list[str]) -> dict[str, set[str]]:
    available = mysql_schema()
    missing = [table for table in allowed_tables if table not in available]
    if missing:
        raise ValueError(f"database is missing required tables: {', '.join(missing)}")
    return {table: set(available[table]) for table in allowed_tables}


def execute_readonly(sql: str, params: dict, max_rows: int) -> tuple[list[str], list[list], bool]:
    with query_connection() as conn:
        with conn.cursor() as cursor:
            compiled, bound = _compile_mysql(sql, params)
            # pymysql treats '%' as a format specifier whenever args are passed,
            # even an empty dict, so a literal LIKE '%x%' must run without args.
            if bound:
                cursor.execute(compiled, bound)
            else:
                cursor.execute(compiled)
            columns = [str(description[0]) for description in (cursor.description or [])]
            rows = cursor.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            return columns, [list(row) for row in rows[:max_rows]], truncated


def check_gold(dataset: dict, schema: dict[str, set[str]]) -> int:
    """Validate that every gold query passes the guardrails and returns rows."""
    failures = 0
    for case in validate_dataset(dataset):
        try:
            sql = validate_sql(str(case["gold_sql"]), schema, max_length=16000, dialect="mysql", max_params=50)
            _, rows, truncated = execute_readonly(sql, dict(case.get("gold_params") or {}), 200)
            if truncated or not rows:
                raise ValueError("no usable rows")
        except Exception as exc:
            failures += 1
            print(f"  {case['id']}: {type(exc).__name__}: {getattr(exc, 'code', '') or exc}")
    total = len(dataset["cases"])
    print(f"Gold integrity: {total - failures}/{total} passed")
    return 0 if failures == 0 else 1


def _build_generate(column_values: dict | None = None):
    if not settings.model_api_key:
        return None
    service = AgentQueryService(
        provider=settings.model_provider,
        direct_generate=generate_sql,
        langchain_generate=generate_sql_langchain,
        build_langchain=build_sql_generator,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model=settings.model_name,
        timeout=settings.model_timeout_seconds,
        system_prompt=compose_system_prompt(),
        prompt_version=DEFAULT_PROMPT_VERSION,
    )

    async def generate_current(**kwargs):
        context = dict(kwargs.get("context") or {})
        if column_values:
            context["column_values"] = column_values
        return await service.generate(
            question=kwargs["question"],
            schema=kwargs["schema"],
            context=context,
            dialect=kwargs["dialect"],
            direct_kwargs={
                "model": settings.model_name,
                "timeout": settings.model_timeout_seconds,
                "api_key": settings.model_api_key,
                "base_url": settings.model_base_url,
                "cache_ttl": 0,
                "cache_max_entries": 0,
                "system_prompt": compose_system_prompt(),
                "prompt_version": DEFAULT_PROMPT_VERSION,
            },
        )

    return generate_current


async def _run(dataset: dict, schema: dict[str, set[str]], limit: int | None, grounding_enabled: bool = True) -> dict:
    selected = dict(dataset)
    if limit:
        selected["cases"] = dataset["cases"][:limit]
    column_values = {}
    configured = dataset.get("value_columns") or []
    if configured and grounding_enabled:
        from app.mysql_db import distinct_values
        from app.services.value_grounding_service import ValueGroundingService

        grounding = ValueGroundingService(
            distinct_values,
            max_columns=settings.value_grounding_max_columns,
            max_values=settings.value_grounding_max_values,
            max_value_chars=settings.value_grounding_max_value_chars,
            ttl_seconds=settings.value_grounding_ttl_seconds,
        )
        column_values = grounding.context(
            datasource_id="business-gold-mysql",
            allowed_tables=set(schema),
            configured=configured,
        )
        print(f"grounding: {sum(len(v) for v in column_values.values())} columns")
    return await evaluate_business_gold_mysql(
        dataset=selected,
        schema=schema,
        execute=execute_readonly,
        generate=_build_generate(column_values),
        model={
            "provider": settings.model_provider,
            "model": settings.model_name,
            "prompt_version": DEFAULT_PROMPT_VERSION,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "data" / "business_gold_mysql.json")
    parser.add_argument("--output", type=Path, default=Path("artifacts/business-gold-mysql.json"))
    parser.add_argument("--check-gold", action="store_true", help="only validate gold SQL against the live database")
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N cases")
    parser.add_argument("--require-external", action="store_true")
    parser.add_argument("--no-value-grounding", action="store_true", help="disable column-value grounding for a paired comparison")
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        schema = restricted_schema(list(dataset.get("schema_tables") or []))
        if args.check_gold:
            return check_gold(dataset, schema)
        report = asyncio.run(_run(dataset, schema, args.limit, grounding_enabled=not args.no_value_grounding))
        _write_atomic(args.output, report)
    except (OSError, ValueError) as exc:
        print(f"Business gold (MySQL) evaluation failed: {exc}")
        return 2
    except Exception:
        print("Business gold (MySQL) evaluation failed: unexpected runtime failure")
        return 2
    metrics = report["metrics"]
    print(f"Business gold report written: {args.output}")
    print(
        "cases={cases} gold={gold_integrity_rate} generation={generation_success_rate} "
        "guardrail={guardrail_pass_rate} execution={execution_success_rate} "
        "result_match={result_exact_match_rate}".format(**metrics)
    )
    if report["attribution"]:
        print("attribution: " + json.dumps(report["attribution"], ensure_ascii=False))
    if not report["local_gate"]["passed"]:
        return 1
    if report["external_validation"] == "not_run":
        print("External model validation was not run because no model API key is configured.")
        return 3 if args.require_external else 0
    return 0 if report["external_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
