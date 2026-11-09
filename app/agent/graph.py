"""Bounded query-agent graph boundary.

The adapter deliberately keeps execution callbacks outside the graph so the
existing authorization and SQL guardrails remain the only database boundary.
It can be replaced by a LangGraph StateGraph without changing API contracts.
"""
from collections.abc import Callable
from typing import Any
from .state import QueryAgentState
from .contracts import AnalysisPlan, ResultArtifact, ResultExplanation, VerificationResult, merge_sql_params, parse_analysis_plan, parse_derived_analysis, parse_result_artifact, parse_result_explanation, parse_sql_plan, parse_verification_result
import inspect

from .reflection import AgentTerminalError, ReflectionAction, ReflectionPolicy, ReflectionResult, build_reflection_prompt, error_code_from_exception, public_error_message, restore_reflection_budget

class QueryAgentGraph:
    def __init__(self, *, generate: Callable, validate: Callable, execute: Callable, resolve_semantics: Callable | None = None, plan: Callable | None = None, verify: Callable | None = None, analyze: Callable | None = None, explain: Callable | None = None, materialize: Callable | None = None, pre_execute: Callable | None = None, max_repairs: int = 2, checkpoint: Callable[[dict[str, Any]], None] | None = None, reflection_policy: ReflectionPolicy | None = None, max_schema_expansions: int = 1, max_execution_retries: int = 1, max_reflection_steps: int = 12):
        self.generate = generate
        self.validate = validate
        self.execute = execute
        self.resolve_semantics = resolve_semantics or (lambda _: {})
        self.plan = plan or (lambda question, _: AnalysisPlan(objective=question))
        self.verify = verify or (lambda _: VerificationResult(accepted=True))
        self.analyze = analyze
        self.explain = explain or (lambda _: ResultExplanation(summary="查询完成", row_count=0))
        self.materialize = materialize or (lambda _result, _: ResultArtifact(name="查询结果", idempotency_key="query-result"))
        self.pre_execute = pre_execute
        self.max_repairs = max(0, max_repairs)
        self.checkpoint = checkpoint
        self.reflection_policy = reflection_policy or ReflectionPolicy()
        self.max_schema_expansions = max(0, max_schema_expansions)
        self.max_execution_retries = max(0, max_execution_retries)
        self.max_reflection_steps = max(1, max_reflection_steps)

    def _checkpoint(self, state: QueryAgentState) -> None:
        if self.checkpoint:
            try:
                self.checkpoint(state.snapshot())
            except Exception as exc:
                state.status = "checkpoint_failed"
                state.error = str(exc)
                raise

    async def ainvoke(self, state: QueryAgentState) -> dict:
        state.max_repair_attempts = self.max_repairs
        provided_sql = bool(state.sql)
        provided_params = dict(state.params)
        budget = restore_reflection_budget(
            state.reflection_budget,
            max_sql_repairs=self.max_repairs,
            max_schema_expansions=self.max_schema_expansions,
            max_execution_retries=self.max_execution_retries,
            max_total_steps=self.max_reflection_steps,
        )
        state.reflection_budget = budget.model_dump()
        state.status = "resolving_semantics"
        state.semantic_context = self.resolve_semantics(state.question)
        self._checkpoint(state)
        state.status = "planning"
        state.analysis_plan = parse_analysis_plan(self.plan(state.question, state.semantic_context)).model_dump()
        self._checkpoint(state)
        while True:
            state.repair_attempts = budget.sql_repairs
            state.reflection_budget = budget.model_dump()
            self._checkpoint(state)
            failure_stage = "generate"
            try:
                if not state.sql:
                    generation_question = build_reflection_prompt(state.question, state.reflection)
                    plan = parse_sql_plan(await self.generate(generation_question))
                    state.sql = plan.sql
                    merged_params = merge_sql_params(provided_params, plan.params)
                    state.params.clear()
                    state.params.update(merged_params)
                    state.intent = plan.intent
                    state.confidence = plan.confidence
                    state.needs_approval = plan.needs_approval
                failure_stage = "validate"
                state.sql = self.validate(state.sql)
                if self.pre_execute:
                    failure_stage = "pre_execute"
                    self.pre_execute(state.sql, state.confidence, state.needs_approval, state.analysis_plan)
                failure_stage = "execute"
                result = self.execute(state.sql, state.params)
                failure_stage = "verify"
                verification_value = self.verify(result)
                if inspect.isawaitable(verification_value):
                    verification_value = await verification_value
                verification = parse_verification_result(verification_value)
                state.verification = verification.model_dump()
                if not verification.accepted:
                    error = ValueError(f"E_RESULT_VERIFICATION:{verification.reason or '查询结果验证失败'}")
                    error.code = verification.error_code or "E_RESULT_VERIFICATION"
                    error.retryable = verification.retryable
                    error.reflection = verification.reflection
                    raise error
            except Exception as exc:
                if getattr(exc, "approval_gate", False):
                    state.status = "approval_required"
                    self._checkpoint(state)
                    raise
                error_code = error_code_from_exception(exc)
                default_retryable = failure_stage in {"generate", "validate", "verify"}
                embedded_reflection = getattr(exc, "reflection", None)
                if embedded_reflection:
                    reflection = ReflectionResult.model_validate(embedded_reflection)
                    if reflection.retryable and not budget.can_take(reflection.action):
                        reflection = self.reflection_policy.from_failure(
                            stage=failure_stage,
                            error_code=error_code,
                            message=str(exc),
                            retryable=True,
                            provided_sql=provided_sql,
                            budget=budget,
                        )
                else:
                    reflection = self.reflection_policy.from_failure(
                        stage=failure_stage,
                        error_code=error_code,
                        message=str(exc),
                        retryable=bool(getattr(exc, "retryable", default_retryable)),
                        provided_sql=provided_sql,
                        budget=budget,
                    )
                budget.consume(reflection.action)
                state.reflection = reflection.model_dump(mode="json")
                state.reflection_history.append(dict(state.reflection))
                state.reflection_budget = budget.model_dump()
                state.repair_attempts = budget.sql_repairs
                state.error_code = error_code
                state.error = public_error_message(
                    code=error_code,
                    failure_type=reflection.failure_type,
                    action=reflection.action,
                )
                state.status = "reflecting"
                self._checkpoint(state)

                if reflection.action == ReflectionAction.REGENERATE_SQL:
                    state.sql = ""
                    state.params.clear()
                    state.params.update(provided_params)
                    continue
                if reflection.action == ReflectionAction.RETRIEVE_MORE_SCHEMA:
                    state.status = "resolving_semantics"
                    state.semantic_context = self.resolve_semantics(
                        build_reflection_prompt(state.question, reflection)
                    )
                    state.sql = ""
                    state.params.clear()
                    state.params.update(provided_params)
                    self._checkpoint(state)
                    continue
                if reflection.action == ReflectionAction.RETRY_EXECUTION:
                    continue

                if reflection.action == ReflectionAction.ASK_CLARIFICATION:
                    state.status = "clarification_required"
                elif reflection.action == ReflectionAction.REQUIRE_APPROVAL:
                    state.status = "approval_required"
                else:
                    state.status = "failed"
                self._checkpoint(state)
                raise AgentTerminalError(state.error_code, state.error, status=state.status) from exc
            else:
                state.error = ""
                state.error_code = ""
                break
        try:
            if state.analysis_plan.get("requires_multiple_steps") and self.analyze:
                state.status = "analyzing"
                state.derived_analysis = parse_derived_analysis(self.analyze(result, state.analysis_plan)).model_dump()
                self._checkpoint(state)
            state.status = "explaining"
            state.explanation = parse_result_explanation(self.explain(result)).model_dump()
            if state.derived_analysis.get("summary"):
                state.explanation["summary"] = state.derived_analysis["summary"]
            self._checkpoint(state)
            state.status = "materializing"
            artifact = parse_result_artifact(self.materialize(result, state.explanation))
            state.artifacts = [artifact.model_dump()]
            state.status = "completed"
            # Persistence failures must never enter the SQL repair loop.
            self._checkpoint(state)
        except Exception as exc:
            if state.status != "checkpoint_failed":
                state.status = "postprocess_failed" if state.status != "completed" else "checkpoint_failed"
            state.error = str(exc)
            raise
        return {"state": state, "result": result}
