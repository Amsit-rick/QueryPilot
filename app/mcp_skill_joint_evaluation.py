"""Offline contract evaluation for Skill routing and Agent MCP enrichment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.mcp_agent_enrichment_service import (
    McpAgentResultError,
    McpEnrichmentLimits,
    route_agent_mcp_tools,
    sanitize_agent_mcp_result,
)


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_mcp_skill_joint_cases(
    *,
    cases: list[dict[str, Any]],
    skill_service,
    dataset_version: str = "adhoc",
    limits: McpEnrichmentLimits | None = None,
) -> dict[str, Any]:
    ids = [str(case.get("id", "")).strip() for case in cases]
    if not cases or any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("evaluation case IDs must be non-empty and unique")
    if len(cases) > 100:
        raise ValueError("joint evaluation is limited to 100 cases")
    active_limits = limits or McpEnrichmentLimits(max_tools=4)
    results = []
    skill_matches: list[bool] = []
    tool_matches: list[bool] = []
    safety_matches: list[bool] = []

    for case in cases:
        question = str(case.get("question", "")).strip()
        if not question or len(question) > 2000:
            raise ValueError(f"invalid question in evaluation case {case['id']}")
        expected_tools = tuple(str(item) for item in case.get("expected_tools", []))
        if any(tool not in {"catalog.search", "metric.lookup", "lineage.get", "policy.explain"} for tool in expected_tools):
            raise ValueError(f"invalid expected tool in evaluation case {case['id']}")

        skill_route = skill_service.route(question)
        selected_skill = skill_route.get("selected_skill")
        skill_id = selected_skill.get("id") if selected_skill else None
        tools = route_agent_mcp_tools(question, max_tools=active_limits.max_tools)
        skill_match = skill_id == case.get("expected_skill_id")
        tool_match = tools == expected_tools

        status = "not_used"
        error_code = ""
        security_findings = 0
        if tools:
            simulated_error = str(case.get("simulate_error_code", "")).strip()
            if simulated_error:
                status = "degraded"
                error_code = simulated_error[:80]
            else:
                try:
                    _sanitized, findings, _digest = sanitize_agent_mcp_result(
                        case.get("result", {"isError": False, "content": []}),
                        tool=tools[0],
                        limits=active_limits,
                    )
                    status = "completed"
                    security_findings = len(findings)
                except McpAgentResultError as exc:
                    status = "degraded"
                    error_code = exc.code

        expected_status = str(case.get("expected_status", "not_used"))
        expected_error_code = str(case.get("expected_error_code", ""))
        expected_min_findings = max(0, int(case.get("expected_min_security_findings", 0)))
        safety_match = (
            status == expected_status
            and error_code == expected_error_code
            and security_findings >= expected_min_findings
        )
        passed = skill_match and tool_match and safety_match
        skill_matches.append(skill_match)
        tool_matches.append(tool_match)
        safety_matches.append(safety_match)
        results.append(
            {
                "id": case["id"],
                "category": str(case.get("category", "uncategorized"))[:80],
                "skill_id": skill_id,
                "tools": list(tools),
                "status": status,
                "error_code": error_code,
                "security_findings": security_findings,
                "skill_match": skill_match,
                "tool_match": tool_match,
                "safety_match": safety_match,
                "passed": passed,
            }
        )

    return {
        "evaluation_type": "mcp_skill_joint_contract",
        "dataset_version": dataset_version,
        "summary": {
            "cases": len(results),
            "passed": sum(item["passed"] for item in results),
            "skill_route_match_rate": _rate(skill_matches),
            "tool_route_match_rate": _rate(tool_matches),
            "safety_behavior_match_rate": _rate(safety_matches),
        },
        "cases": results,
        "limitations": [
            "该评测只验证确定性 Skill/MCP 路由、结果清洗和故障降级契约。",
            "该评测不连接远程 MCP Server，不衡量网络、认证、容量或第三方实现行为。",
            "该评测不衡量 SQL 语义、执行结果或真实业务指标口径准确率。",
        ],
    }


def load_mcp_skill_joint_cases(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(payload.get("version", "unknown")), list(payload.get("cases", []))


__all__ = ["evaluate_mcp_skill_joint_cases", "load_mcp_skill_joint_cases"]
