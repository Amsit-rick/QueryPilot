"""Structured reflection contracts and deterministic routing policy.

The policy never weakens authorization, SQL safety, cost, or approval gates.
It only decides which already-controlled workflow step may run next.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReflectionFailureType(str, Enum):
    NONE = "none"
    SYNTAX = "syntax"
    SCHEMA = "schema"
    RELATIONSHIP = "relationship"
    AGGREGATION_GRAIN = "aggregation_grain"
    BUSINESS_AMBIGUITY = "business_ambiguity"
    EMPTY_RESULT = "empty_result"
    RESULT_SHAPE = "result_shape"
    EXECUTION_TRANSIENT = "execution_transient"
    PERMISSION = "permission"
    SECURITY = "security"
    COST = "cost"
    UNKNOWN = "unknown"


class ReflectionAction(str, Enum):
    ACCEPT = "accept"
    REGENERATE_SQL = "regenerate_sql"
    RETRIEVE_MORE_SCHEMA = "retrieve_more_schema"
    RETRY_EXECUTION = "retry_execution"
    ASK_CLARIFICATION = "ask_clarification"
    REQUIRE_APPROVAL = "require_approval"
    FAIL = "fail"


class AgentTerminalError(ValueError):
    """Safe, stable error contract shared by all Agent runtimes."""

    def __init__(self, code: str, message: str, *, status: str = "failed"):
        self.code = str(code or "E_AGENT_LOOP")[:80]
        self.message = str(message or "查询处理失败")[:300]
        self.status = status
        super().__init__(f"{self.code}:{self.message}")


class ReflectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    failure_type: ReflectionFailureType
    reason_codes: list[str] = Field(min_length=1, max_length=8)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    action: ReflectionAction
    repair_instruction: str | None = Field(default=None, max_length=500)
    retryable: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, values: list[str]) -> list[str]:
        normalized = [str(value).strip().upper() for value in values]
        if any(not value or len(value) > 80 for value in normalized):
            raise ValueError("reflection reason codes must be 1 to 80 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("reflection reason codes must be unique")
        return normalized

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, values: list[str]) -> list[str]:
        normalized = [sanitize_reflection_text(value) for value in values]
        if any(not value for value in normalized):
            raise ValueError("reflection evidence must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_transition(self) -> "ReflectionResult":
        repair_actions = {
            ReflectionAction.REGENERATE_SQL,
            ReflectionAction.RETRIEVE_MORE_SCHEMA,
            ReflectionAction.RETRY_EXECUTION,
        }
        if self.accepted:
            if self.failure_type != ReflectionFailureType.NONE or self.action != ReflectionAction.ACCEPT:
                raise ValueError("accepted reflection must use none/accept")
            if self.retryable:
                raise ValueError("accepted reflection cannot be retryable")
        else:
            if self.failure_type == ReflectionFailureType.NONE or self.action == ReflectionAction.ACCEPT:
                raise ValueError("rejected reflection must describe a failure and non-accept action")
            if self.retryable != (self.action in repair_actions):
                raise ValueError("retryable must match an automatic repair action")
        return self


class ReflectionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql_repairs: int = Field(default=0, ge=0)
    schema_expansions: int = Field(default=0, ge=0)
    execution_retries: int = Field(default=0, ge=0)
    total_steps: int = Field(default=0, ge=0)
    max_sql_repairs: int = Field(default=2, ge=0, le=20)
    max_schema_expansions: int = Field(default=1, ge=0, le=10)
    max_execution_retries: int = Field(default=1, ge=0, le=10)
    max_total_steps: int = Field(default=12, ge=1, le=100)

    def can_take(self, action: ReflectionAction) -> bool:
        if self.total_steps >= self.max_total_steps:
            return False
        if action == ReflectionAction.REGENERATE_SQL:
            return self.sql_repairs < self.max_sql_repairs
        if action == ReflectionAction.RETRIEVE_MORE_SCHEMA:
            return self.schema_expansions < self.max_schema_expansions
        if action == ReflectionAction.RETRY_EXECUTION:
            return self.execution_retries < self.max_execution_retries
        return True

    def consume(self, action: ReflectionAction) -> None:
        self.total_steps += 1
        if action == ReflectionAction.REGENERATE_SQL:
            self.sql_repairs += 1
        elif action == ReflectionAction.RETRIEVE_MORE_SCHEMA:
            self.schema_expansions += 1
        elif action == ReflectionAction.RETRY_EXECUTION:
            self.execution_retries += 1


_SECURITY_CODES = {
    "E_PROMPT_INJECTION",
    "E_READONLY_VIOLATION",
}
_PERMISSION_CODES = {
    "E_PERMISSION_DENIED",
    "E_DATASOURCE_FORBIDDEN",
    "E_ASSET_FORBIDDEN",
    "E_TASK_NOT_FOUND",
    "E_INTEGRATION_TOOL_NOT_FOUND",
}
_SCHEMA_CODES = {
    "E_HALLUCINATED_FIELD",
    "E_AMBIGUOUS_FIELD",
    "E_GATEWAY_TABLE",
    "E_GATEWAY_SCHEMA",
}
_RELATIONSHIP_CODES = {
    "E_CARTESIAN_JOIN",
    "E_RELATIONSHIP_AMBIGUOUS",
    "E_RELATIONSHIP_MISSING",
}
_SYNTAX_CODES = {
    "E_QUERY_INVALID",
    "E_QUERY_ERROR",
    "E_QUERY_EMPTY",
    "E_QUERY_PARAM_CONFLICT",
}
_COST_LIMIT_CODES = {
    "E_QUERY_COST_LIMIT",
}
_COST_FAILURE_CODES = {
    "E_QUERY_COST",
    "E_BUDGET_EXCEEDED",
}
_TRANSIENT_CODES = {
    "E_TIMEOUT",
    "E_GATEWAY_QUERY",
    "E_GATEWAY_COST",
    "E_BUDGET_UNAVAILABLE",
}


class ReflectionPolicy:
    """Map deterministic failure evidence to a bounded workflow action."""

    def from_failure(
        self,
        *,
        stage: str,
        error_code: str,
        message: str,
        retryable: bool,
        provided_sql: bool,
        budget: ReflectionBudget,
    ) -> ReflectionResult:
        code = _normalize_code(error_code, message)
        failure_type = self._failure_type(stage=stage, code=code)

        if provided_sql:
            return self._failure(
                failure_type=failure_type,
                reason_code="CALLER_SQL_REJECTED",
                message=message,
                action=ReflectionAction.FAIL,
                instruction="用户显式 SQL 校验失败，禁止由模型静默替换。",
            )
        if failure_type in {ReflectionFailureType.SECURITY, ReflectionFailureType.PERMISSION}:
            return self._failure(
                failure_type=failure_type,
                reason_code=code,
                message=message,
                action=ReflectionAction.FAIL,
                instruction="硬安全或权限门禁拒绝，不允许自动修复。",
            )
        if code in _COST_LIMIT_CODES:
            return self._failure(
                failure_type=failure_type,
                reason_code=code,
                message=message,
                action=ReflectionAction.REQUIRE_APPROVAL,
                instruction="查询成本超过门禁，需要改写查询或人工审批。",
            )
        if code in _COST_FAILURE_CODES:
            return self._failure(
                failure_type=ReflectionFailureType.COST,
                reason_code=code,
                message=message,
                action=ReflectionAction.FAIL,
                instruction="成本或预算服务失败关闭，不创建虚假审批。",
            )
        if not retryable:
            return self._failure(
                failure_type=failure_type,
                reason_code=code,
                message=message,
                action=ReflectionAction.FAIL,
                instruction="该失败被上游标记为不可重试。",
            )

        preferred_action = self._preferred_action(failure_type)
        if budget.can_take(preferred_action):
            return self._failure(
                failure_type=failure_type,
                reason_code=code,
                message=message,
                action=preferred_action,
                instruction=self._repair_instruction(preferred_action),
            )
        return self._failure(
            failure_type=failure_type,
            reason_code="REFLECTION_BUDGET_EXHAUSTED",
            message=message,
            action=ReflectionAction.FAIL,
            instruction="自动反思预算已耗尽，停止执行。",
        )

    @staticmethod
    def _failure_type(*, stage: str, code: str) -> ReflectionFailureType:
        if code in _SECURITY_CODES:
            return ReflectionFailureType.SECURITY
        if code in _PERMISSION_CODES or code.endswith("_FORBIDDEN"):
            return ReflectionFailureType.PERMISSION
        if code in _SCHEMA_CODES:
            return ReflectionFailureType.SCHEMA
        if code in _RELATIONSHIP_CODES:
            return ReflectionFailureType.RELATIONSHIP
        if code in _COST_LIMIT_CODES or code in _COST_FAILURE_CODES:
            return ReflectionFailureType.COST
        if code in _TRANSIENT_CODES or stage == "execute" and code == "E_QUERY_ERROR":
            return ReflectionFailureType.EXECUTION_TRANSIENT
        if code == "E_RESULT_VERIFICATION":
            return ReflectionFailureType.RESULT_SHAPE
        if code in _SYNTAX_CODES or stage in {"generate", "validate"}:
            return ReflectionFailureType.SYNTAX
        return ReflectionFailureType.UNKNOWN

    @staticmethod
    def _preferred_action(failure_type: ReflectionFailureType) -> ReflectionAction:
        if failure_type == ReflectionFailureType.SCHEMA:
            return ReflectionAction.RETRIEVE_MORE_SCHEMA
        if failure_type == ReflectionFailureType.EXECUTION_TRANSIENT:
            return ReflectionAction.RETRY_EXECUTION
        if failure_type == ReflectionFailureType.BUSINESS_AMBIGUITY:
            return ReflectionAction.ASK_CLARIFICATION
        return ReflectionAction.REGENERATE_SQL

    @staticmethod
    def _repair_instruction(action: ReflectionAction) -> str:
        return {
            ReflectionAction.REGENERATE_SQL: "根据错误码修正 SQL；仍只生成一条只读 SELECT。",
            ReflectionAction.RETRIEVE_MORE_SCHEMA: "只在当前授权范围内扩大 Schema 召回，再重新生成 SQL。",
            ReflectionAction.RETRY_EXECUTION: "保持 SQL 和参数不变，仅重试一次数据库执行。",
        }[action]

    @staticmethod
    def _failure(
        *,
        failure_type: ReflectionFailureType,
        reason_code: str,
        message: str,
        action: ReflectionAction,
        instruction: str,
    ) -> ReflectionResult:
        return ReflectionResult(
            accepted=False,
            failure_type=failure_type,
            reason_codes=[reason_code],
            evidence=[f"error_code={reason_code}"],
            action=action,
            repair_instruction=instruction,
            retryable=action
            in {
                ReflectionAction.REGENERATE_SQL,
                ReflectionAction.RETRIEVE_MORE_SCHEMA,
                ReflectionAction.RETRY_EXECUTION,
            },
            confidence=1.0,
        )


def error_code_from_exception(exc: Exception, default: str = "E_QUERY_ERROR") -> str:
    if isinstance(exc, PermissionError):
        return "E_PERMISSION_DENIED"
    code = str(getattr(exc, "code", "") or "").strip().upper()
    if code:
        return code
    match = re.search(r"\b(E_[A-Z0-9_]+)\b", str(exc).upper())
    return match.group(1) if match else default


def build_reflection_prompt(question: str, reflection: dict[str, Any] | ReflectionResult | None) -> str:
    if not reflection:
        return question
    value = reflection if isinstance(reflection, ReflectionResult) else ReflectionResult.model_validate(reflection)
    evidence = "；".join(value.evidence[:3])
    codes = ",".join(value.reason_codes)
    return (
        f"{question}\n\n[结构化反思反馈]\n"
        f"failure_type={value.failure_type.value}\n"
        f"action={value.action.value}\n"
        f"reason_codes={codes}\n"
        f"evidence={evidence}\n"
        f"repair_instruction={value.repair_instruction or ''}\n"
        "不得放宽权限、只读、安全、成本或审批规则。"
    )


def public_error_message(
    *,
    code: str,
    failure_type: ReflectionFailureType | str,
    action: ReflectionAction | str,
) -> str:
    normalized_code = str(code or "E_AGENT_LOOP").upper()
    failure = ReflectionFailureType(failure_type)
    next_action = ReflectionAction(action)
    exact = {
        "E_MODEL_CONFIG": "未配置 MODEL_API_KEY；请在 .env 中配置模型密钥后重启服务",
        "E_PROMPT_INJECTION": "检测到试图绕过查询安全边界的指令",
        "E_READONLY_VIOLATION": "SQL 违反只读安全规则，已拒绝执行",
        "E_QUERY_INVALID": "生成或提交的 SQL 未通过语法校验",
        "E_HALLUCINATED_FIELD": "SQL 引用了不存在或未授权的表或字段",
        "E_AMBIGUOUS_FIELD": "SQL 字段引用存在歧义",
        "E_CARTESIAN_JOIN": "SQL JOIN 缺少受控关联条件",
        "E_TIMEOUT": "数据库查询暂时超时，请稍后重试",
        "E_QUERY_COST": "查询成本评估不可用，已失败关闭",
        "E_QUERY_COST_LIMIT": "查询成本超过执行门禁",
        "E_RESULT_VERIFICATION": "查询结果未通过结构验证",
        "E_PERMISSION_DENIED": "当前身份无权执行该操作",
    }
    if normalized_code in exact:
        return exact[normalized_code]
    if next_action == ReflectionAction.ASK_CLARIFICATION:
        return "查询条件或业务口径不明确，需要用户补充信息"
    if next_action == ReflectionAction.REQUIRE_APPROVAL:
        return "查询需要人工审批后才能继续"
    return {
        ReflectionFailureType.PERMISSION: "当前身份无权执行该操作",
        ReflectionFailureType.SECURITY: "查询未通过安全校验",
        ReflectionFailureType.COST: "查询未通过成本门禁",
        ReflectionFailureType.SCHEMA: "查询引用的 Schema 信息无效或不足",
        ReflectionFailureType.RELATIONSHIP: "查询关系路径无效或不明确",
        ReflectionFailureType.EXECUTION_TRANSIENT: "查询执行失败，请稍后重试",
        ReflectionFailureType.RESULT_SHAPE: "查询结果未通过结构验证",
        ReflectionFailureType.SYNTAX: "SQL 生成或校验失败",
    }.get(failure, "查询处理失败")


def _normalize_code(error_code: str, message: str) -> str:
    code = str(error_code or "").strip().upper()
    if code:
        return code
    match = re.search(r"\b(E_[A-Z0-9_]+)\b", str(message).upper())
    return match.group(1) if match else "E_QUERY_ERROR"


def sanitize_reflection_text(value: Any) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}",
        "Bearer [REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@",
        lambda match: match.group(0).split("://", 1)[0] + "://[REDACTED]@",
        text,
    )
    text = re.sub(
        r"(?i)(['\"]?(?:password|passwd|pwd|dsn|api[_-]?key|access[_-]?token|authorization|[a-z0-9_]*secret[a-z0-9_]*|[a-z0-9_]*token[a-z0-9_]*)['\"]?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)",
        lambda match: f"{match.group(1)}[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(action|failure_type|reason_codes|repair_instruction)\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED_INSTRUCTION]",
        text,
    )
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:300]


def restore_reflection_budget(
    payload: dict[str, Any] | None,
    *,
    max_sql_repairs: int,
    max_schema_expansions: int,
    max_execution_retries: int,
    max_total_steps: int,
) -> ReflectionBudget:
    """Restore consumed counters while enforcing current server-side limits."""

    source = payload if isinstance(payload, dict) else {}

    def counter(name: str, fail_closed: int) -> int:
        try:
            return max(0, int(source.get(name, 0)))
        except (TypeError, ValueError):
            return fail_closed

    return ReflectionBudget(
        sql_repairs=counter("sql_repairs", max_sql_repairs),
        schema_expansions=counter("schema_expansions", max_schema_expansions),
        execution_retries=counter("execution_retries", max_execution_retries),
        total_steps=counter("total_steps", max_total_steps),
        max_sql_repairs=max(0, max_sql_repairs),
        max_schema_expansions=max(0, max_schema_expansions),
        max_execution_retries=max(0, max_execution_retries),
        max_total_steps=max(1, max_total_steps),
    )
