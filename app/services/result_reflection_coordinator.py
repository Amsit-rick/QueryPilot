"""Coordinate deterministic quality rules, rollout mode, and semantic critic."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from app.agent.contracts import VerificationResult
from app.agent.reflection import ReflectionAction, ReflectionFailureType, ReflectionResult
from app.services.reflection_mode_service import resolve_reflection_mode
from app.services.result_quality_service import ResultQualityService
from app.services.semantic_critic_service import SemanticCriticService


class ResultReflectionCoordinator:
    def __init__(
        self,
        *,
        quality: ResultQualityService,
        critic: SemanticCriticService,
        critic_enabled: bool = True,
        requested_mode: str,
        gate_report_path: str | Path,
    ):
        self.quality = quality
        self.critic = critic
        self.critic_enabled = bool(critic_enabled)
        self.mode = resolve_reflection_mode(requested_mode, report_path=gate_report_path)

    async def verify(
        self,
        *,
        question: str,
        sql: str,
        columns: list[str] | tuple[str, ...],
        rows: list[list[Any]] | list[tuple[Any, ...]],
        truncated: bool,
        authorized_schema: dict[str, set[str] | list[str] | tuple[str, ...]],
        relationships: list[dict[str, Any]],
        schema_details: dict[str, Any],
        expected_dimensions: Iterable[str] = (),
        expected_metrics: Iterable[str] = (),
        explain_summary: dict[str, Any] | None = None,
        provided_sql: bool = False,
    ) -> VerificationResult:
        assessment = self.quality.assess(
            question=question,
            sql=sql,
            columns=columns,
            rows=rows,
            truncated=truncated,
            authorized_schema=authorized_schema,
            relationships=relationships,
            schema_details=schema_details,
            expected_dimensions=expected_dimensions,
            expected_metrics=expected_metrics,
            explain_summary=explain_summary,
        )
        effective_mode = self.mode.effective_mode
        if assessment.outcome == "accepted":
            return VerificationResult(
                accepted=True,
                retryable=False,
                source="deterministic",
                mode=effective_mode,
                gate_reason=self.mode.reason,
                warning_codes=assessment.warning_codes,
                reason_codes=list(assessment.reflection.reason_codes),
                evidence=list(assessment.reflection.evidence),
            )

        suggestion = assessment.reflection
        source = "deterministic"
        if assessment.outcome == "uncertain" and self.critic_enabled:
            suggestion = await self.critic.review(assessment)
            source = "semantic_critic"

        if effective_mode == "off":
            return VerificationResult(
                accepted=True,
                retryable=False,
                source=source,
                mode="off",
                gate_reason=self.mode.reason,
                warning_codes=list(assessment.reason_codes),
            )
        if effective_mode == "shadow":
            return VerificationResult(
                accepted=True,
                retryable=False,
                source=source,
                mode="shadow",
                gate_reason=self.mode.reason,
                reason_codes=list(suggestion.reason_codes),
                evidence=list(suggestion.evidence),
                suggested_reflection=suggestion.model_dump(mode="json"),
            )

        if provided_sql and suggestion.action in {
            ReflectionAction.REGENERATE_SQL,
            ReflectionAction.RETRIEVE_MORE_SCHEMA,
            ReflectionAction.RETRY_EXECUTION,
        }:
            suggestion = ReflectionResult(
                accepted=False,
                failure_type=suggestion.failure_type,
                reason_codes=["CALLER_SQL_RESULT_REJECTED"],
                evidence=["caller-provided SQL failed deterministic result-quality verification"],
                action=ReflectionAction.FAIL,
                repair_instruction="用户显式 SQL 的结果质量校验失败，禁止由模型静默替换。",
                retryable=False,
                confidence=1.0,
            )

        if suggestion.accepted:
            return VerificationResult(
                accepted=True,
                retryable=False,
                source=source,
                mode="enforced",
                gate_reason=self.mode.reason,
                reason_codes=list(suggestion.reason_codes),
                evidence=list(suggestion.evidence),
            )
        return VerificationResult(
            accepted=False,
            reason="；".join(suggestion.reason_codes),
            retryable=suggestion.retryable,
            confidence=suggestion.confidence,
            error_code=suggestion.reason_codes[0] if suggestion.reason_codes else "E_RESULT_VERIFICATION",
            source=source,
            mode="enforced",
            gate_reason=self.mode.reason,
            reason_codes=list(suggestion.reason_codes),
            evidence=list(suggestion.evidence),
            reflection=suggestion.model_dump(mode="json"),
        )

