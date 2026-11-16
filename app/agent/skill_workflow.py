"""Platform-owned executable workflow profiles for approved declarative Skills.

Tenant-authored ``workflow_steps`` are presentation metadata only. This module
maps a small allowlist of Skill IDs to fixed nodes, tools and budgets owned by
the application, so publishing a Skill can never introduce executable code.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any


EXECUTABLE_SKILL_STEPS = (
    "clarify",
    "retrieve_metric",
    "retrieve_schema",
    "validate_grain",
    "generate_sql",
    "validate_sql",
    "execute",
    "verify_result",
    "explain",
)

_ALLOWED_TOOLS = (
    "metric.lookup",
    "schema.inspect",
    "query.generate",
    "query.validate",
    "query.execute",
    "result.verify",
    "result.explain",
)

_BUDGETS = {
    "max_external_tool_calls": 2,
    "max_model_calls": 3,
    "max_total_tokens": 50000,
    "max_cost_usd": 1.0,
}

_STEP_POLICIES = {
    "clarify": {"timeout_seconds": 1, "max_calls": 1, "allowed_tools": []},
    "retrieve_metric": {"timeout_seconds": 10, "max_calls": 1, "allowed_tools": ["metric.lookup"]},
    "retrieve_schema": {"timeout_seconds": 10, "max_calls": 1, "allowed_tools": ["schema.inspect"]},
    "validate_grain": {"timeout_seconds": 1, "max_calls": 1, "allowed_tools": []},
    "generate_sql": {"timeout_seconds": 30, "max_calls": 3, "allowed_tools": ["query.generate"]},
    "validate_sql": {"timeout_seconds": 5, "max_calls": 3, "allowed_tools": ["query.validate"]},
    "execute": {"timeout_seconds": 30, "max_calls": 2, "allowed_tools": ["query.execute"]},
    "verify_result": {"timeout_seconds": 10, "max_calls": 3, "allowed_tools": ["result.verify"]},
    "explain": {"timeout_seconds": 5, "max_calls": 1, "allowed_tools": ["result.explain"]},
}

_PROFILES = {
    "sales-analysis": {"profile_id": "sales-analysis-v1", "requires_time_grain": False},
    "trend-analysis": {"profile_id": "trend-analysis-v1", "requires_time_grain": True},
}

_VAGUE_QUESTIONS = {
    "销售",
    "销售分析",
    "查询销售",
    "分析销售",
    "趋势",
    "趋势分析",
    "查看趋势",
    "分析趋势",
}


class SkillWorkflowBudgetError(ValueError):
    """Stable, non-retryable failure raised before a model budget is exceeded."""

    retryable = False

    def __init__(self, code: str, message: str):
        self.code = str(code or "E_SKILL_BUDGET")[:80]
        self.message = str(message or "Skill 工作流预算校验失败")[:300]
        super().__init__(f"{self.code}:{self.message}")


def _budget_number(budgets: dict[str, Any], name: str, *, integer: bool = False) -> float | int:
    value = budgets.get(name)
    if isinstance(value, bool):
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_CONFIG", f"Skill 工作流预算 {name} 配置非法")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_CONFIG", f"Skill 工作流预算 {name} 配置非法") from exc
    if not math.isfinite(number) or (integer and not number.is_integer()):
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_CONFIG", f"Skill 工作流预算 {name} 配置非法")
    parsed = int(number) if integer else number
    if parsed < 0 or (integer and parsed == 0):
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_CONFIG", f"Skill 工作流预算 {name} 配置非法")
    return parsed


def enforce_skill_model_budget(
    workflow: dict[str, Any],
    *,
    model_calls: int,
    estimated_total_tokens: int,
    estimated_cost_usd: float,
) -> None:
    """Fail closed before the next model call exceeds an executable Skill contract."""

    if isinstance(workflow, dict) and workflow.get("status") == "not_applicable":
        return
    contract = workflow.get("contract") if isinstance(workflow, dict) else None
    budgets = contract.get("budgets") if isinstance(contract, dict) else None
    if not isinstance(budgets, dict):
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_CONFIG", "Skill 工作流缺少平台预算合同")
    if any(isinstance(value, bool) for value in (model_calls, estimated_total_tokens, estimated_cost_usd)):
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_INPUT", "Skill 工作流预算用量参数非法")
    try:
        calls = int(model_calls)
        tokens = int(estimated_total_tokens)
        cost = float(estimated_cost_usd)
    except (TypeError, ValueError) as exc:
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_INPUT", "Skill 工作流预算用量参数非法") from exc
    if (
        calls < 0
        or tokens < 0
        or cost < 0
        or not math.isfinite(float(model_calls))
        or not math.isfinite(float(estimated_total_tokens))
        or not math.isfinite(cost)
        or float(model_calls) != calls
        or float(estimated_total_tokens) != tokens
    ):
        raise SkillWorkflowBudgetError("E_SKILL_BUDGET_INPUT", "Skill 工作流预算用量参数非法")

    max_calls = _budget_number(budgets, "max_model_calls", integer=True)
    max_tokens = _budget_number(budgets, "max_total_tokens", integer=True)
    max_cost = _budget_number(budgets, "max_cost_usd")
    if calls >= max_calls:
        raise SkillWorkflowBudgetError("E_SKILL_MODEL_BUDGET", "Skill 工作流模型调用次数已达到平台上限")
    if tokens > max_tokens:
        raise SkillWorkflowBudgetError("E_SKILL_TOKEN_BUDGET", "Skill 工作流预计 Token 用量超过平台上限")
    if cost > max_cost:
        raise SkillWorkflowBudgetError("E_SKILL_COST_BUDGET", "Skill 工作流预计模型费用超过平台上限")


def _evidence(step: str, status: str, reason_codes: list[str] | None = None, output: dict | None = None) -> dict:
    return {
        "step": step,
        "status": status,
        "reason_codes": [str(code)[:80] for code in (reason_codes or [])[:8]],
        "output": dict(output or {}),
    }


def _base_result(*, status: str, error_code: str = "", message: str = "", contract=None, evidence=None) -> dict:
    return {
        "status": status,
        "error_code": str(error_code)[:80],
        "message": str(message)[:300],
        "contract": deepcopy(contract) if contract is not None else None,
        "evidence": [dict(item) for item in (evidence or [])],
    }


def _normalized_question(question: str) -> str:
    return re.sub(r"[\s，。！？,.!?：:；;]+", "", str(question or "")).strip()[:2000]


def _time_grain(question: str) -> tuple[str, str]:
    patterns = (
        ("day", r"按日|每天|每日|逐日|日趋势"),
        ("week", r"按周|每周|逐周|周趋势"),
        ("month", r"按月|每月|逐月|月度|月趋势"),
        ("quarter", r"按季|每季|逐季|季度|季趋势"),
        ("year", r"按年|每年|逐年|年度|年趋势"),
    )
    for grain, pattern in patterns:
        if re.search(pattern, str(question or ""), re.I):
            return grain, "TIME_GRAIN_EXPLICIT"
    return "month", "TIME_GRAIN_DEFAULTED"


def _contract(skill_id: str, release_version: str) -> dict:
    profile = _PROFILES[skill_id]
    return {
        "profile_id": profile["profile_id"],
        "profile_version": "1.0.0",
        "skill_id": skill_id,
        "skill_release_version": str(release_version or "unknown")[:32],
        "steps": list(EXECUTABLE_SKILL_STEPS),
        "allowed_tools": list(_ALLOWED_TOOLS),
        "step_policies": deepcopy(_STEP_POLICIES),
        "budgets": dict(_BUDGETS),
    }


class ExecutableSkillWorkflowService:
    """Compile approved Skill IDs into deterministic preflight evidence."""

    def prepare(self, question: str, semantic_context: dict[str, Any], *, provided_sql: bool) -> dict:
        skill = semantic_context.get("skill") if isinstance(semantic_context, dict) else None
        skill_id = str((skill or {}).get("id") or "")
        if skill_id not in _PROFILES:
            return _base_result(status="not_applicable")

        contract = _contract(skill_id, str((skill or {}).get("version") or ""))
        mcp = semantic_context.get("mcp_enrichment") or {}
        mcp_items = mcp.get("items") if isinstance(mcp, dict) else []
        mcp_items = mcp_items if isinstance(mcp_items, list) else []
        if len(mcp_items) > contract["budgets"]["max_external_tool_calls"]:
            return _base_result(
                status="failed",
                error_code="E_SKILL_TOOL_BUDGET",
                message="Skill 外部工具调用数量超过平台预算",
                contract=contract,
                evidence=[
                    _evidence("clarify", "completed", ["QUESTION_ACCEPTED"]),
                    _evidence(
                        "retrieve_metric",
                        "failed",
                        ["EXTERNAL_TOOL_BUDGET_EXCEEDED"],
                        {"call_count": len(mcp_items)},
                    ),
                ],
            )

        evidence = []
        if not provided_sql and _normalized_question(question) in _VAGUE_QUESTIONS:
            evidence.append(_evidence("clarify", "clarification_required", ["BUSINESS_QUESTION_AMBIGUOUS"]))
            return _base_result(
                status="clarification_required",
                error_code="E_SKILL_CLARIFICATION_REQUIRED",
                message="请补充需要分析的指标、维度或时间范围",
                contract=contract,
                evidence=evidence,
            )
        evidence.append(
            _evidence(
                "clarify",
                "completed",
                ["CALLER_SQL_PROVIDED" if provided_sql else "QUESTION_ACCEPTED"],
            )
        )

        external_metric_count = sum(
            isinstance(item, dict) and item.get("tool") == "metric.lookup"
            for item in mcp_items
        )
        local_metric_count = len(semantic_context.get("metrics") or []) + sum(
            isinstance(item, dict) and item.get("kind") == "metric"
            for item in (semantic_context.get("semantics") or [])
        )
        if external_metric_count:
            evidence.append(_evidence("retrieve_metric", "completed", ["EXTERNAL_METRIC_CONTEXT"], {"source": "external_mcp", "count": external_metric_count}))
        elif local_metric_count:
            evidence.append(_evidence("retrieve_metric", "completed", ["LOCAL_METRIC_CONTEXT"], {"source": "local_semantics", "count": local_metric_count}))
        else:
            evidence.append(_evidence("retrieve_metric", "skipped", ["METRIC_CONTEXT_NOT_AVAILABLE"], {"source": "question_only", "count": 0}))

        schema_retrieval = semantic_context.get("schema_retrieval") or {}
        selected_tables = schema_retrieval.get("selected_tables") if isinstance(schema_retrieval, dict) else []
        selected_tables = selected_tables if isinstance(selected_tables, list) else []
        evidence.append(
            _evidence(
                "retrieve_schema",
                "completed" if selected_tables else "degraded",
                ["SCHEMA_CONTEXT_READY" if selected_tables else "SCHEMA_CONTEXT_EMPTY"],
                {"selected_table_count": len(selected_tables)},
            )
        )

        if _PROFILES[skill_id]["requires_time_grain"]:
            grain, reason = _time_grain(question)
            evidence.append(_evidence("validate_grain", "completed", [reason], {"time_grain": grain}))
        else:
            evidence.append(_evidence("validate_grain", "skipped", ["TIME_GRAIN_NOT_REQUIRED"]))
        return _base_result(status="ready", contract=contract, evidence=evidence)


def finalize_skill_workflow(
    workflow: dict[str, Any],
    *,
    provided_sql: bool,
    final_status: str,
    error_code: str = "",
) -> dict[str, Any]:
    """Return replay-safe step evidence without prompts, SQL or tool payloads."""
    result = deepcopy(workflow or _base_result(status="not_applicable"))
    if result.get("status") == "not_applicable":
        return result
    evidence = list(result.get("evidence") or [])
    present = {str(item.get("step")) for item in evidence if isinstance(item, dict)}
    for step in ("generate_sql", "validate_sql", "execute", "verify_result", "explain"):
        if step in present:
            continue
        if final_status == "completed":
            if step == "generate_sql" and provided_sql:
                evidence.append(_evidence(step, "skipped", ["CALLER_SQL_PROVIDED"]))
            else:
                evidence.append(_evidence(step, "completed", ["STEP_COMPLETED"]))
        else:
            reason = "UPSTREAM_CLARIFICATION" if final_status == "clarification_required" else "UPSTREAM_TERMINATED"
            evidence.append(_evidence(step, "skipped", [reason]))
    result["evidence"] = evidence
    result["status"] = str(final_status)[:64]
    if error_code:
        result["error_code"] = str(error_code)[:80]
    return result


__all__ = [
    "EXECUTABLE_SKILL_STEPS",
    "ExecutableSkillWorkflowService",
    "SkillWorkflowBudgetError",
    "enforce_skill_model_budget",
    "finalize_skill_workflow",
]
