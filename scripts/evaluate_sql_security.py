"""Evaluate the deterministic SQL boundary against a versioned local corpus."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.guardrails import QueryError, validate_sql


def evaluate(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    schema = {table: set(columns) for table, columns in dataset["schema"].items()}
    results = []
    for case in dataset["cases"]:
        allowed = False
        error_code = None
        try:
            validate_sql(case["sql"], schema, dialect=case["dialect"])
            allowed = True
        except QueryError as exc:
            error_code = exc.code
        passed = allowed == bool(case["allowed"])
        if not case["allowed"]:
            passed = passed and error_code == case.get("error_code")
        results.append({
            "id": case["id"],
            "dialect": case["dialect"],
            "passed": passed,
            "actual_allowed": allowed,
            "actual_error_code": error_code,
        })
    categories = Counter(item["id"].split("-", 1)[0] for item in results if item["passed"])
    passed = sum(item["passed"] for item in results)
    return {
        "format": "query-pilot-sql-security-eval",
        "dataset_version": dataset["version"],
        "total": len(results),
        "passed": passed,
        "corpus_accuracy": passed / len(results) if results else 0,
        "passed_categories": dict(sorted(categories.items())),
        "limitations": [
            "This is a versioned regression corpus, not a percentage of all possible SQL attacks.",
            "Database privileges, network controls and live-instance tests remain separate evidence.",
        ],
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Query Pilot SQL safety corpus")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "data" / "sql_security_eval.json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(args.dataset)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
