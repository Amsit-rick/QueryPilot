"""Deterministic cross-capability quality evaluation.

This suite complements the SQL fixture evaluator with small, local contract
checks for the capabilities added by the SQLBot-inspired roadmap. It does not
call an LLM, a remote service, or a production database.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from pydantic import ValidationError

from app.domain.chart_spec import parse_chart_spec
from app.domain.data_gateway import GatewayError
from app.domain.task import DataTask
from app.gateways.factory import build_gateway_registry
from app.gateways.remote_sql import normalize_remote_sql, select_tree, compile_remote_sql
from app.guardrails import QueryError, validate_sql
from app.policy import apply_policy, filter_schema
from app.repositories.memory_semantic_repository import InMemorySemanticRepository
from app.services.read_only_integration_service import TOOLS
from app.services.semantic_service import SemanticService
from app.services.context_service import build_controlled_expansion
from app.services.task_context_service import build_follow_up_context
from app.services.analysis_service import describe_result
from app.services.feedback_service import sanitize_question, sanitize_sql_template
from app.services.quality_gate_service import anomaly_gate_reasons, evaluate_pre_execution


def _semantic_case(case: dict[str, Any]) -> dict[str, Any]:
    repository = InMemorySemanticRepository()
    service = SemanticService(repository)
    service.create(
        tenant_id="default",
        datasource_id="sqlite-demo",
        kind="sql_example",
        name=case["name"],
        description="",
        owner_id="eval",
        definition={
            "question": case["example_question"],
            "sql": "SELECT region FROM sales",
            "tables": ["sales"],
            "columns": ["region"],
            "aliases": case.get("aliases", []),
            "keywords": case.get("keywords", []),
        },
    )
    matches = service.search_matches(
        case["query"],
        "default",
        "sqlite-demo",
        {"sales": {"region"}},
    )
    passed = bool(matches) and matches[0].entry.name == case["expected_name"]
    return {"passed": passed, "top_match": matches[0].entry.name if matches else None}


def _value_grounding_case(case: dict[str, Any]) -> dict[str, Any]:
    service = SemanticService(InMemorySemanticRepository())
    service.create(
        tenant_id="default",
        datasource_id="sqlite-demo",
        kind="value_dictionary",
        name="区域值词典",
        owner_id="eval",
        definition={
            "target_table": "sales",
            "target_column": "region",
            "tables": ["sales"],
            "columns": ["region"],
            "values": [
                {"value": "EAST", "aliases": ["华东", "东区"]},
                {"value": "WEST", "aliases": ["华西", "西区"]},
            ],
        },
    )
    matches = service.search_value_matches(
        case["query"],
        "default",
        "sqlite-demo",
        case["allowed_schema"],
    )
    expansion = build_controlled_expansion([], matches)
    actual_values = [item["value"] for item in matches]
    serialized = json.dumps({"matches": matches, "expansion": expansion}, ensure_ascii=False)
    passed = actual_values == case["expected_values"]
    for forbidden in case.get("forbidden_values", []):
        passed = passed and forbidden not in serialized
    return {"passed": passed, "values": actual_values, "expansion": expansion}


def _permission_case(case: dict[str, Any]) -> dict[str, Any]:
    identity = {"roles": case["roles"], "datasource": "default"}
    visible = filter_schema(case["schema"], identity)
    if case.get("expected_tables") is not None:
        passed = visible == case["expected_tables"]
    else:
        try:
            apply_policy(case["sql"], identity)
        except Exception as exc:  # QueryError is intentionally checked by code.
            passed = getattr(exc, "code", "") == case["expected_error"]
        else:
            passed = False
    return {"passed": passed, "visible": visible}


def _follow_up_case(case: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "task": SimpleNamespace(id="task-eval", datasource_id="sqlite-demo"),
        "steps": [
            SimpleNamespace(
                kind="understand_question",
                input={"question": "各区域销售额"},
                output={},
            ),
            SimpleNamespace(
                kind="query",
                status="completed",
                input={},
                output={
                    "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region",
                    "columns": ["region", "total"],
                    "row_count": 2,
                    "rows": [["华东", 1960]],
                },
            ),
        ],
    }
    context = build_follow_up_context(detail)
    serialized = json.dumps(context, ensure_ascii=False)
    passed = (
        context["task_id"] == case["task_id"]
        and context["datasource_id"] == case["datasource_id"]
        and len(context["turns"]) == 1
        and "rows" not in serialized
    )
    return {"passed": passed, "context": context}


def _chart_case(case: dict[str, Any]) -> dict[str, Any]:
    try:
        parse_chart_spec(case["spec"], columns=case["columns"])
    except (ValidationError, ValueError) as exc:
        passed = case["mode"] == "reject"
        return {"passed": passed, "error": str(exc)}
    passed = case["mode"] == "accept"
    return {"passed": passed, "error": None}


def _approval_case(case: dict[str, Any]) -> dict[str, Any]:
    task = DataTask(question="评测审批", tenant_id="default", owner_id="eval", datasource_id="sqlite-demo")
    for status in ("planning", "running", "approval_required", "running", "completed"):
        task.transition(status)
    return {"passed": task.status == case["expected_status"], "status": task.status}


def _integration_case(case: dict[str, Any]) -> dict[str, Any]:
    names = {tool["name"] for tool in TOOLS}
    all_read_only = all(tool.get("read_only") is True for tool in TOOLS)
    passed = names == set(case["expected_tools"]) and all_read_only and "query.execute" not in names
    return {"passed": passed, "tools": sorted(names)}


def _gateway_case(case: dict[str, Any]) -> dict[str, Any]:
    try:
        build_gateway_registry([{"id": "unsupported", "backend": case["backend"]}])
    except GatewayError as exc:
        return {"passed": exc.code == case["expected_error"], "error": exc.code}
    return {"passed": False, "error": None}


def _connector_case(case: dict[str, Any]) -> dict[str, Any]:
    identity = {"roles": case.get("roles", ["admin"]), "datasource": "default"}
    schema = filter_schema({"sales": ["region", "amount"]}, identity)
    try:
        canonical = normalize_remote_sql(case["sql"], case["backend"])
        validate_sql(canonical, {table: set(columns) for table, columns in schema.items()})
        apply_policy(canonical, identity)
        compiled = compile_remote_sql(select_tree(canonical, "sqlite"), case["backend"], case.get("params", {}))
        return {"passed": not case.get("expected_error") and case.get("expected_sql_fragment", "SELECT") in compiled, "execution": "not_run"}
    except QueryError as exc:
        return {"passed": exc.code == case.get("expected_error"), "error": exc.code, "execution": "not_run"}


def _analysis_case(case: dict[str, Any]) -> dict[str, Any]:
    result = describe_result(
        (case["columns"], case["rows"], bool(case.get("truncated", False))),
        {"analysis_mode": case["mode"]},
    ).model_dump(mode="json")
    counts = case.get("expected_counts", {})
    passed = all(len(result.get(name, [])) == count for name, count in counts.items())
    expected_limitation = case.get("expected_limitation")
    if expected_limitation:
        passed = passed and any(expected_limitation in value for value in result["limitations"])
    return {"passed": passed, "analysis": result, "execution": "deterministic_local"}


def _quality_case(case: dict[str, Any]) -> dict[str, Any]:
    kind = case["kind"]
    if kind == "pre_execution_gate":
        decision = evaluate_pre_execution(
            case["sql"],
            confidence=case["confidence"],
            model_requires_approval=case.get("model_requires_approval", False),
            requested_rows=case.get("requested_rows", 100),
            low_confidence_threshold=0.65,
            high_risk_row_threshold=5000,
        )
        return {"passed": list(decision.reasons) == case["expected_reasons"], "reasons": list(decision.reasons)}
    if kind == "anomaly_gate":
        reasons = anomaly_gate_reasons({"anomalies": case["anomalies"]})
        return {"passed": list(reasons) == case["expected_reasons"], "reasons": list(reasons)}
    if kind == "feedback_redaction":
        question = sanitize_question(case["question"])
        sql = sanitize_sql_template(case["sql"])
        passed = (
            case["forbidden_question_value"] not in question
            and case.get("forbidden_secret_value", "") not in question
            and case["forbidden_sql_value"] not in sql
        )
        return {"passed": passed, "question": question, "sql_template": sql, "contains_result_rows": False}
    return {"passed": False, "failure_reason": "unsupported_quality_case"}


_EVALUATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "semantic": _semantic_case,
    "value_grounding": _value_grounding_case,
    "permission": _permission_case,
    "follow_up": _follow_up_case,
    "chart": _chart_case,
    "approval": _approval_case,
    "integration": _integration_case,
    "gateway": _gateway_case,
    "connector": _connector_case,
    "analysis": _analysis_case,
    "quality": _quality_case,
}


def run_capability_eval(path: str = "./data/capability_eval.json") -> dict[str, Any]:
    """Run the local cross-capability fixture set and return a versioned report."""
    dataset = json.loads(Path(path).read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for case in dataset.get("cases", []):
        capability = str(case.get("capability", ""))
        evaluator = _EVALUATORS.get(capability)
        if evaluator is None:
            result = {"passed": False, "failure_reason": "unsupported_capability"}
        else:
            result = evaluator(case)
        results.append({
            "id": case.get("id", "unknown"),
            "capability": capability,
            "passed": bool(result.get("passed")),
            "details": result,
        })

    by_capability: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_capability.setdefault(result["capability"], []).append(result)
    passed = sum(1 for result in results if result["passed"])
    return {
        "dataset_version": dataset["version"],
        "total": len(results),
        "passed": passed,
        "accuracy": passed / len(results) if results else 0,
        "sections": {
            capability: {
                "total": len(items),
                "passed": sum(1 for item in items if item["passed"]),
                "accuracy": sum(1 for item in items if item["passed"]) / len(items) if items else 0,
                "results": items,
            }
            for capability, items in sorted(by_capability.items())
        },
        "results": results,
        "external_validation": "not_run",
    }


if __name__ == "__main__":
    print(json.dumps(run_capability_eval(), ensure_ascii=False, indent=2))
