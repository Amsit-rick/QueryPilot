"""Persist reflection decisions without raw SQL, parameters, rows, or errors."""

from __future__ import annotations

import hashlib
from typing import Any

from app.agent.reflection import ReflectionAction, ReflectionFailureType, ReflectionResult
from app.services.task_service import TaskService


class ReflectionTraceService:
    def __init__(self, tasks: TaskService):
        self.tasks = tasks

    def persist(
        self,
        *,
        task_id: str,
        start_sequence: int,
        reflections: list[dict[str, Any]],
        sql: str,
        model: str,
        prompt_version: str,
        final_status: str,
        mode: str = "enforced",
        source: str = "deterministic",
    ) -> int:
        sequence = start_sequence
        sql_sha256 = hashlib.sha256(str(sql).encode("utf-8")).hexdigest()
        for attempt, value in enumerate(reflections, start=1):
            reflection = ReflectionResult.model_validate(value)
            payload = {
                "failure_type": reflection.failure_type.value,
                "reason_codes": list(reflection.reason_codes),
                "action": reflection.action.value,
                "attempt": attempt,
                "evidence": list(reflection.evidence),
                "sql_sha256": sql_sha256,
                "model": str(model)[:128],
                "prompt_version": str(prompt_version)[:128],
                "final_status": str(final_status)[:64],
                "mode": str(mode)[:32],
                "source": str(source)[:32],
            }
            self.tasks.append_step(
                task_id=task_id,
                kind="reflection",
                sequence=sequence,
                status="completed",
                output=payload,
                retry_count=attempt - 1,
            )
            self.tasks.append_event(task_id=task_id, kind="reflection_recorded", payload=payload)
            sequence += 1
        return sequence

    @staticmethod
    def viewer_projection(payload: dict[str, Any]) -> dict[str, Any]:
        failure_type = str(payload.get("failure_type", "unknown"))
        action = str(payload.get("action", "fail"))
        return {
            "failure_type": failure_type,
            "reason_codes": [str(item)[:80] for item in payload.get("reason_codes", [])[:8]],
            "action": action,
            "attempt": max(1, int(payload.get("attempt", 1) or 1)),
            "summary": _public_summary(failure_type, action),
        }


def _public_summary(failure_type: str, action: str) -> str:
    problem = {
        ReflectionFailureType.SYNTAX.value: "SQL 语法问题",
        ReflectionFailureType.SCHEMA.value: "Schema 匹配问题",
        ReflectionFailureType.RELATIONSHIP.value: "表关系风险",
        ReflectionFailureType.AGGREGATION_GRAIN.value: "聚合粒度风险",
        ReflectionFailureType.BUSINESS_AMBIGUITY.value: "业务口径歧义",
        ReflectionFailureType.EMPTY_RESULT.value: "空结果风险",
        ReflectionFailureType.RESULT_SHAPE.value: "结果结构异常",
        ReflectionFailureType.EXECUTION_TRANSIENT.value: "数据库瞬时错误",
        ReflectionFailureType.PERMISSION.value: "权限限制",
        ReflectionFailureType.SECURITY.value: "安全规则冲突",
        ReflectionFailureType.COST.value: "查询成本风险",
    }.get(failure_type, "查询质量问题")
    action_text = {
        ReflectionAction.REGENERATE_SQL.value: "已重新生成查询",
        ReflectionAction.RETRIEVE_MORE_SCHEMA.value: "已扩大授权范围内的 Schema 召回",
        ReflectionAction.RETRY_EXECUTION.value: "已按原查询重试",
        ReflectionAction.ASK_CLARIFICATION.value: "需要补充业务口径",
        ReflectionAction.REQUIRE_APPROVAL.value: "需要人工审批",
        ReflectionAction.FAIL.value: "已停止执行",
        ReflectionAction.ACCEPT.value: "验证通过",
    }.get(action, "已记录处理动作")
    return f"检测到{problem}，{action_text}。"

