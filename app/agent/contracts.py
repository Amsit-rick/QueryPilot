"""Typed contracts exchanged between LangChain components and the graph."""
import json
import math
import re
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_PLAN_PARAMS = 50
MAX_PARAM_STRING_LENGTH = 2000
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AnalysisMode(StrEnum):
    NONE = "none"
    DESCRIPTIVE_STATISTICS = "descriptive_statistics"
    TREND = "trend"
    PERIOD_COMPARISON = "period_comparison"
    ANOMALY_DETECTION = "anomaly_detection"
    AUTO_INSIGHTS = "auto_insights"


class SqlPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(min_length=1)
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    intent: str = "data_query"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_approval: bool = False

    @field_validator("params")
    @classmethod
    def validate_params(cls, params):
        if len(params) > MAX_PLAN_PARAMS:
            raise ValueError("SQL plan contains too many parameters")
        for name, value in params.items():
            if not _PARAM_NAME.fullmatch(name):
                raise ValueError("SQL plan parameter name is invalid")
            if isinstance(value, str) and len(value) > MAX_PARAM_STRING_LENGTH:
                raise ValueError("SQL plan parameter string is too long")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("SQL plan parameter must be finite")
        return params


def merge_sql_params(
    caller_params: dict[str, Any] | None,
    generated_params: dict[str, Any] | None,
    *,
    max_params: int = MAX_PLAN_PARAMS,
) -> dict[str, Any]:
    """Merge typed model parameters without overwriting caller intent."""
    caller = dict(caller_params or {})
    generated = dict(generated_params or {})
    conflicts = sorted(
        name for name in caller.keys() & generated.keys()
        if caller[name] != generated[name]
    )
    if conflicts:
        raise ValueError(f"E_QUERY_PARAM_CONFLICT:模型参数与请求参数冲突：{', '.join(conflicts)}")
    merged = {**generated, **caller}
    if len(merged) > max(0, int(max_params)):
        raise ValueError("E_QUERY_LIMIT:SQL 参数数量超过限制")
    return merged


class VerificationResult(BaseModel):
    accepted: bool
    reason: str = ""
    retryable: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    error_code: str = "E_RESULT_VERIFICATION"
    source: Literal["structural", "deterministic", "semantic_critic"] = "structural"
    mode: Literal["off", "shadow", "enforced"] = "off"
    gate_reason: str = ""
    reason_codes: list[str] = Field(default_factory=list, max_length=8)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    warning_codes: list[str] = Field(default_factory=list, max_length=8)
    reflection: dict[str, Any] | None = None
    suggested_reflection: dict[str, Any] | None = None


class AnalysisPlan(BaseModel):
    objective: str = Field(min_length=1)
    intent: str = "data_query"
    steps: list[Literal["generate_sql", "validate_sql", "execute_query", "verify_result", "analyze_result"]] = Field(default_factory=lambda: ["generate_sql", "validate_sql", "execute_query", "verify_result"], max_length=5)
    requires_multiple_steps: bool = False
    analysis_mode: AnalysisMode = AnalysisMode.NONE

    @model_validator(mode="after")
    def validate_controlled_path(self):
        base = ["generate_sql", "validate_sql", "execute_query", "verify_result"]
        expected = base + (["analyze_result"] if self.analysis_mode != AnalysisMode.NONE else [])
        if self.steps != expected:
            raise ValueError("analysis steps do not match the allowed execution path")
        if self.requires_multiple_steps != (self.analysis_mode != AnalysisMode.NONE):
            raise ValueError("analysis mode and multi-step flag are inconsistent")
        return self


class TrendInsight(BaseModel):
    column: str = Field(min_length=1, max_length=256)
    time_column: str = Field(min_length=1, max_length=256)
    start_period: str = Field(min_length=1, max_length=128)
    end_period: str = Field(min_length=1, max_length=128)
    start_value: float
    end_value: float
    absolute_change: float
    percent_change: float | None = None
    direction: Literal["up", "down", "flat"]
    points: int = Field(ge=2, le=10000)


class PeriodComparison(BaseModel):
    kind: Literal["previous_period", "year_over_year"]
    column: str = Field(min_length=1, max_length=256)
    current_period: str = Field(min_length=1, max_length=128)
    baseline_period: str = Field(min_length=1, max_length=128)
    current_value: float
    baseline_value: float
    absolute_change: float
    percent_change: float | None = None


class AnomalyInsight(BaseModel):
    column: str = Field(min_length=1, max_length=256)
    period: str = Field(min_length=1, max_length=128)
    value: float
    baseline: float
    score: float = Field(gt=0)
    method: Literal["mad", "median_deviation"]


class DerivedAnalysis(BaseModel):
    mode: AnalysisMode
    summary: str = Field(min_length=1)
    metrics: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    time_column: str | None = None
    trends: list[TrendInsight] = Field(default_factory=list, max_length=20)
    comparisons: list[PeriodComparison] = Field(default_factory=list, max_length=40)
    anomalies: list[AnomalyInsight] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class ResultExplanation(BaseModel):
    summary: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    truncated: bool = False
    method: str = "deterministic"


class ResultArtifact(BaseModel):
    kind: str = "table"
    name: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1)


def parse_sql_plan(value) -> SqlPlan:
    if isinstance(value, SqlPlan):
        return value
    if isinstance(value, dict):
        return SqlPlan.model_validate(value)
    text = str(value).strip().strip("`").strip()
    if text.lower().startswith("sql"):
        text = text[3:].strip()
    try:
        return SqlPlan.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        return SqlPlan(sql=text)


def parse_verification_result(value) -> VerificationResult:
    if isinstance(value, VerificationResult):
        return value
    if isinstance(value, bool):
        return VerificationResult(accepted=value)
    return VerificationResult.model_validate(value)


def parse_analysis_plan(value) -> AnalysisPlan:
    if isinstance(value, AnalysisPlan):
        return value
    return AnalysisPlan.model_validate(value)


def parse_derived_analysis(value) -> DerivedAnalysis:
    if isinstance(value, DerivedAnalysis):
        return value
    return DerivedAnalysis.model_validate(value)


def parse_result_explanation(value) -> ResultExplanation:
    if isinstance(value, ResultExplanation):
        return value
    return ResultExplanation.model_validate(value)


def parse_result_artifact(value) -> ResultArtifact:
    if isinstance(value, ResultArtifact):
        return value
    return ResultArtifact.model_validate(value)
