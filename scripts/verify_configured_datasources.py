"""Verify every configured query datasource without exposing credentials."""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.datasources import (
    datasource_schema,
    execute_datasource,
    list_datasources,
    test_connection,
)


def verify_datasource(
    datasource: dict,
    *,
    health_check: Callable[[dict], dict] = test_connection,
    schema_loader: Callable[[dict], dict] = datasource_schema,
    executor: Callable = execute_datasource,
) -> dict:
    datasource_id = str(datasource.get("id") or "unknown")
    backend = str(datasource.get("backend") or "unknown")
    health = health_check(datasource)
    if not health.get("ok"):
        return {"id": datasource_id, "backend": backend, "ok": False, "stage": "health"}

    schema = schema_loader(datasource)
    sales_table = next((name for name in schema if str(name).lower() == "sales"), None)
    if not sales_table:
        return {"id": datasource_id, "backend": backend, "ok": False, "stage": "schema"}
    columns = {str(name).lower() for name in schema[sales_table]}
    if not {"region", "amount"}.issubset(columns):
        return {"id": datasource_id, "backend": backend, "ok": False, "stage": "schema"}

    result_columns, rows, truncated = executor(
        datasource,
        "SELECT region, amount FROM sales ORDER BY id",
        {},
        10,
    )
    return {
        "id": datasource_id,
        "backend": backend,
        "ok": bool(result_columns) and bool(rows),
        "stage": "query",
        "row_count": len(rows),
        "truncated": bool(truncated),
    }


def verify_all(*, wait_seconds: int = 120, poll_seconds: float = 3.0) -> list[dict]:
    datasources = list_datasources()
    deadline = time.monotonic() + max(wait_seconds, 0)
    pending = {str(item.get("id")): item for item in datasources}
    results: dict[str, dict] = {}

    while pending:
        for datasource_id, datasource in list(pending.items()):
            try:
                result = verify_datasource(datasource)
            except Exception:
                result = {
                    "id": datasource_id,
                    "backend": str(datasource.get("backend") or "unknown"),
                    "ok": False,
                    "stage": "exception",
                }
            results[datasource_id] = result
            if result["ok"]:
                pending.pop(datasource_id)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(max(poll_seconds, 0.1))
    return [results[str(item.get("id"))] for item in datasources]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify configured Query Pilot datasources")
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    results = verify_all(wait_seconds=args.wait_seconds)
    for result in results:
        status = "READY" if result["ok"] else "FAILED"
        print(
            f"[{status}] {result['id']} backend={result['backend']} "
            f"stage={result['stage']} rows={result.get('row_count', 0)}"
        )
    return 0 if results and all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
