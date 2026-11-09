"""Run deterministic multi-table relationship accuracy cases against SQLite."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _fixture() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id));
        CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), amount REAL NOT NULL);
        CREATE TABLE payments(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id), amount REAL NOT NULL);
        INSERT INTO customers VALUES (1, '甲公司'), (2, '乙公司'), (3, '丙公司');
        INSERT INTO orders VALUES (1, 1), (2, 2), (3, 99);
        INSERT INTO order_items VALUES (1, 1, 10), (2, 1, 20), (3, 2, 5), (4, 3, 7);
        INSERT INTO payments VALUES (1, 1, 20), (2, 1, 10), (3, 2, 5);
        PRAGMA foreign_keys=ON;
    """)
    return conn


def evaluate(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = []
    conn = _fixture()
    actual_relationships = {
        ("orders", "customer_id", "customers", "id"),
        ("order_items", "order_id", "orders", "id"),
        ("payments", "order_id", "orders", "id"),
    }
    try:
        for case in payload["cases"]:
            rows = [list(row) for row in conn.execute(case["expected_sql"]).fetchall()]
            business_result_correct = rows == case["expected_rows"]
            naive_rows = None
            duplicate_risk_detected = None
            if case.get("naive_sql"):
                naive_rows = [list(row) for row in conn.execute(case["naive_sql"]).fetchall()]
                duplicate_risk_detected = naive_rows != case["expected_rows"]
            expected_relationships = {
                (item["from_table"], *item["from_columns"], item["to_table"], *item["to_columns"])
                for item in case.get("expected_relationships", [])
            }
            relationships_correct = expected_relationships.issubset(actual_relationships)
            join_correct = business_result_correct and relationships_correct
            results.append({"id": case["id"], "passed": join_correct, "sql_syntax_correct": True, "relationships_correct": relationships_correct, "aggregation_grain_correct": duplicate_risk_detected is not False, "business_result_correct": business_result_correct, "duplicate_risk_detected": duplicate_risk_detected, "requires_confirmation": bool(case.get("requires_confirmation", False)), "row_count": len(rows)})
    finally:
        conn.close()
    passed = sum(item["passed"] for item in results)
    return {"version": payload.get("version"), "total": len(results), "passed": passed, "failed": len(results) - passed, "external_validation": "not_run", "results": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/relationship_eval.json")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = evaluate(Path(args.input))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
