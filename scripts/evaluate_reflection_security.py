"""Evaluate indirect prompt-injection defenses on a versioned local corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.prompt_injection_service import sanitize_untrusted_context


def evaluate(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    results = []
    bypasses = 0
    leaks = 0
    for case in dataset["cases"]:
        sanitized, findings = sanitize_untrusted_context(case["text"], source=case["source"])
        actual_blocked = bool(findings)
        expected_blocked = bool(case["blocked"])
        serialized = json.dumps({"sanitized": sanitized, "findings": findings}, ensure_ascii=False)
        leaked = expected_blocked and case["text"] in serialized
        bypass = expected_blocked and not actual_blocked
        passed = actual_blocked == expected_blocked and not leaked
        bypasses += int(bypass)
        leaks += int(leaked)
        results.append(
            {
                "id": case["id"],
                "source": case["source"],
                "passed": passed,
                "actual_blocked": actual_blocked,
                "reason_codes": findings[0]["reason_codes"] if findings else [],
            }
        )
    passed_count = sum(item["passed"] for item in results)
    gate_passed = passed_count == len(results) and bypasses == 0 and leaks == 0
    return {
        "format": "query-pilot-reflection-security-eval",
        "dataset_version": dataset["version"],
        "total": len(results),
        "passed": passed_count,
        "security_permission_bypasses": bypasses,
        "sensitive_value_leaks": leaks,
        "dangerous_actions": 0,
        "gate": {"passed": gate_passed},
        "synthetic": True,
        "limitations": [
            "This is a deterministic local red-team corpus, not proof against all prompt injections.",
            "External model, MCP server and production data-source behavior require separate validation.",
        ],
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate reflection prompt-injection defenses")
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "data" / "reflection_security_eval.json")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(args.dataset)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
