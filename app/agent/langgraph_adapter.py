"""Optional LangGraph implementation of the controlled query workflow."""
from collections.abc import Callable
from typing import Any, TypedDict

from .state import QueryAgentState
from .contracts import AnalysisPlan, ResultArtifact, ResultExplanation, VerificationResult, merge_sql_params, parse_analysis_plan, parse_derived_analysis, parse_result_artifact, parse_result_explanation, parse_sql_plan, parse_verification_result
import inspect

from .reflection import AgentTerminalError, ReflectionAction, ReflectionPolicy, ReflectionResult, build_reflection_prompt, error_code_from_exception, public_error_message, restore_reflection_budget


class _PostprocessError(Exception):
    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


class _CheckpointError(Exception):
    """A checkpoint failure with the last safe, non-result state snapshot."""

    def __init__(self, cause: Exception, snapshot: dict[str, Any]):
        super().__init__(str(cause))
        self.cause = cause
        self.snapshot = snapshot


class AgentGraphState(TypedDict, total=False):
    question: str
    sql: str
    params: dict[str, Any]
    error: str
    error_code: str
    repair_attempts: int
    max_repair_attempts: int
    status: str
    thread_id: str
    result: Any
    provided_sql: bool
    provided_params: dict[str, Any]
    intent: str
    confidence: float
    needs_approval: bool
    semantic_context: dict[str, Any]
    analysis_plan: dict[str, Any]
    verification: dict[str, Any]
    verification_retryable: bool
    failure_stage: str
    failure_retryable: bool
    reflection: dict[str, Any]
    reflection_history: list[dict[str, Any]]
    reflection_budget: dict[str, Any]
    verification_reflection: dict[str, Any]
    derived_analysis: dict[str, Any]
    explanation: dict[str, Any]
    artifacts: list[dict[str, Any]]


class LangGraphQueryAgent:
    def __init__(self, graph: Any, checkpoint: Callable[[dict[str, Any]], None] | None = None):
        self.graph = graph
        self.checkpoint = checkpoint

    async def ainvoke(self, state: QueryAgentState) -> dict[str, Any]:
        payload = state.runtime_payload()
        payload["provided_sql"] = bool(state.sql)
        payload["provided_params"] = dict(state.params)
        if self.checkpoint:
            self.checkpoint(state.snapshot())
        try:
            result = await self.graph.ainvoke(payload)
        except _CheckpointError as exc:
            _apply_snapshot(state, {**exc.snapshot, "status": "checkpoint_failed", "error": str(exc.cause)})
            raise exc.cause
        except _PostprocessError as exc:
            state.status = "postprocess_failed"
            state.error = str(exc.cause)
            raise exc.cause
        state.sql = result.get("sql", "")
        state.params = result.get("params", state.params)
        state.error = result.get("error", "")
        state.error_code = result.get("error_code", "")
        state.repair_attempts = result.get("repair_attempts", 0)
        state.status = result.get("status", "failed")
        state.intent = result.get("intent", state.intent)
        state.confidence = result.get("confidence", state.confidence)
        state.needs_approval = result.get("needs_approval", state.needs_approval)
        state.semantic_context = result.get("semantic_context", {})
        state.analysis_plan = result.get("analysis_plan", {})
        state.verification = result.get("verification", {})
        state.reflection = result.get("reflection", {})
        state.reflection_history = result.get("reflection_history", [])
        state.reflection_budget = result.get("reflection_budget", {})
        state.repair_attempts = int(state.reflection_budget.get("sql_repairs", state.repair_attempts))
        state.derived_analysis = result.get("derived_analysis", {})
        state.explanation = result.get("explanation", {})
        state.artifacts = result.get("artifacts", [])
        if self.checkpoint:
            try:
                self.checkpoint(state.snapshot())
            except Exception as exc:
                state.status = "checkpoint_failed"
                state.error = str(exc)
                raise
        if state.status != "completed":
            code = result.get("error_code") or "E_AGENT_LOOP"
            raise AgentTerminalError(code, state.error or "查询处理失败", status=state.status)
        return {"state": state, "result": result["result"]}


def build_langgraph(*, generate: Callable, validate: Callable, execute: Callable, resolve_semantics: Callable | None = None, plan: Callable | None = None, verify: Callable | None = None, analyze: Callable | None = None, explain: Callable | None = None, materialize: Callable | None = None, pre_execute: Callable | None = None, max_repairs: int = 2, checkpoint: Callable[[dict[str, Any]], None] | None = None, reflection_policy: ReflectionPolicy | None = None, max_schema_expansions: int = 1, max_execution_retries: int = 1, max_reflection_steps: int = 12):
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return None

    policy = reflection_policy or ReflectionPolicy()

    def checkpoint_after(update: dict[str, Any], state: AgentGraphState) -> dict[str, Any]:
        """Persist node progress without ever including raw query results."""
        if not checkpoint:
            return update
        snapshot = dict(state)
        snapshot.update(update)
        snapshot.pop("result", None)
        if snapshot.get("error"):
            reason_codes = snapshot.get("reflection", {}).get("reason_codes", [])
            snapshot["error"] = str(
                snapshot.get("error_code") or (reason_codes[0] if reason_codes else "E_AGENT_ERROR")
            )[:80]
        params = snapshot.pop("params", {})
        snapshot.pop("provided_params", None)
        snapshot["param_names"] = sorted(str(name) for name in params)
        snapshot["param_count"] = len(params)
        try:
            checkpoint(snapshot)
        except Exception as exc:
            raise _CheckpointError(exc, snapshot) from exc
        return update

    def node(name: str, handler: Callable[[AgentGraphState], dict[str, Any]]) -> Callable[[AgentGraphState], dict[str, Any]]:
        def wrapped(state: AgentGraphState) -> dict[str, Any]:
            update = handler(state)
            return checkpoint_after(update, state)

        wrapped.__name__ = name
        return wrapped

    def async_node(name: str, handler: Callable[[AgentGraphState], Any]) -> Callable[[AgentGraphState], Any]:
        async def wrapped(state: AgentGraphState) -> dict[str, Any]:
            update = await handler(state)
            return checkpoint_after(update, state)

        wrapped.__name__ = name
        return wrapped

    def resolve_semantics_node(state: AgentGraphState) -> dict[str, Any]:
        context = resolve_semantics(state["question"]) if resolve_semantics else {}
        return {"semantic_context": context, "status": "semantics_resolved"}

    def plan_node(state: AgentGraphState) -> dict[str, Any]:
        value = plan(state["question"], state.get("semantic_context", {})) if plan else AnalysisPlan(objective=state["question"])
        return {"analysis_plan": parse_analysis_plan(value).model_dump(), "status": "planned"}

    async def generate_node(state: AgentGraphState) -> dict[str, Any]:
        if state.get("sql"):
            return {"status": "generated"}
        try:
            plan = parse_sql_plan(await generate(build_reflection_prompt(state["question"], state.get("reflection"))))
            params = merge_sql_params(state.get("provided_params", {}), plan.params)
            return {"sql": plan.sql, "params": params, "intent": plan.intent, "confidence": plan.confidence, "needs_approval": plan.needs_approval, "status": "generated", "error": "", "error_code": ""}
        except Exception as exc:
            return {
                "error": str(exc),
                "error_code": error_code_from_exception(exc),
                "failure_stage": "generate",
                "failure_retryable": bool(getattr(exc, "retryable", True)),
                "status": "generation_failed",
            }

    def validate_node(state: AgentGraphState) -> dict[str, Any]:
        try:
            sql = validate(state["sql"])
        except Exception as exc:
            if getattr(exc, "approval_gate", False):
                raise
            return {
                "error": str(exc),
                "error_code": error_code_from_exception(exc),
                "failure_stage": "validate",
                "failure_retryable": bool(getattr(exc, "retryable", True)),
                "status": "invalid",
            }
        try:
            if pre_execute:
                pre_execute(sql, float(state.get("confidence", 0)), bool(state.get("needs_approval", False)), state.get("analysis_plan", {}))
        except Exception as exc:
            if getattr(exc, "approval_gate", False):
                raise
            return {
                "error": str(exc),
                "error_code": error_code_from_exception(exc),
                "failure_stage": "pre_execute",
                "failure_retryable": bool(getattr(exc, "retryable", False)),
                "status": "pre_execute_failed",
            }
        return {"sql": sql, "status": "validated", "error": "", "error_code": ""}

    def execute_node(state: AgentGraphState) -> dict[str, Any]:
        try:
            result = execute(state["sql"], state.get("params", {}))
            return {"result": result, "status": "executed", "error": "", "error_code": ""}
        except Exception as exc:
            if getattr(exc, "approval_gate", False):
                raise
            return {
                "error": str(exc),
                "error_code": error_code_from_exception(exc),
                "failure_stage": "execute",
                "failure_retryable": bool(getattr(exc, "retryable", False)),
                "status": "execution_failed",
            }

    async def verify_node(state: AgentGraphState) -> dict[str, Any]:
        value = verify(state.get("result")) if verify else VerificationResult(accepted=True)
        if inspect.isawaitable(value):
            value = await value
        verification = parse_verification_result(value)
        if verification.accepted:
            return {"verification": verification.model_dump(), "verification_reflection": {}, "verification_retryable": False, "status": "verified", "error": "", "error_code": ""}
        return {
            "verification": verification.model_dump(),
            "verification_reflection": verification.reflection or {},
            "verification_retryable": verification.retryable,
            "failure_stage": "verify",
            "failure_retryable": verification.retryable,
            "status": "verification_failed",
            "error": verification.reason or "查询结果验证失败",
            "error_code": verification.error_code or "E_RESULT_VERIFICATION",
        }

    def reflect_node(state: AgentGraphState) -> dict[str, Any]:
        budget = restore_reflection_budget(
            state.get("reflection_budget"),
            max_sql_repairs=max(0, max_repairs),
            max_schema_expansions=max(0, max_schema_expansions),
            max_execution_retries=max(0, max_execution_retries),
            max_total_steps=max(1, max_reflection_steps),
        )
        embedded_reflection = state.get("verification_reflection")
        if embedded_reflection:
            reflection = ReflectionResult.model_validate(embedded_reflection)
            if reflection.retryable and not budget.can_take(reflection.action):
                reflection = policy.from_failure(
                    stage=state.get("failure_stage", "validate"),
                    error_code=state.get("error_code", "E_QUERY_ERROR"),
                    message=state.get("error", "查询处理失败"),
                    retryable=True,
                    provided_sql=bool(state.get("provided_sql")),
                    budget=budget,
                )
        else:
            reflection = policy.from_failure(
                stage=state.get("failure_stage", "validate"),
                error_code=state.get("error_code", "E_QUERY_ERROR"),
                message=state.get("error", "查询处理失败"),
                retryable=bool(state.get("failure_retryable", False)),
                provided_sql=bool(state.get("provided_sql")),
                budget=budget,
            )
        budget.consume(reflection.action)
        reflection_payload = reflection.model_dump(mode="json")
        history = [*state.get("reflection_history", []), reflection_payload]
        update: dict[str, Any] = {
            "reflection": reflection_payload,
            "reflection_history": history,
            "reflection_budget": budget.model_dump(),
            "repair_attempts": budget.sql_repairs,
            "status": "reflected",
            "error": public_error_message(
                code=state.get("error_code", "E_AGENT_LOOP"),
                failure_type=reflection.failure_type,
                action=reflection.action,
            ),
        }
        if reflection.action in {ReflectionAction.REGENERATE_SQL, ReflectionAction.RETRIEVE_MORE_SCHEMA}:
            update.update({"sql": "", "params": dict(state.get("provided_params", {}))})
        elif reflection.action == ReflectionAction.ASK_CLARIFICATION:
            update["status"] = "clarification_required"
        elif reflection.action == ReflectionAction.REQUIRE_APPROVAL:
            update["status"] = "approval_required"
        elif reflection.action == ReflectionAction.FAIL:
            update["status"] = "failed"
        return update

    def retrieve_semantics_node(state: AgentGraphState) -> dict[str, Any]:
        context = resolve_semantics(
            build_reflection_prompt(state["question"], state.get("reflection"))
        ) if resolve_semantics else {}
        return {"semantic_context": context, "status": "semantics_expanded"}

    def route_validation(state: AgentGraphState) -> str:
        return "execute" if state.get("status") == "validated" else "reflect"

    def route_generation(state: AgentGraphState) -> str:
        return "validate" if state.get("status") == "generated" else "reflect"

    def route_execution(state: AgentGraphState) -> str:
        return "verify" if state.get("status") == "executed" else "reflect"

    def route_verification(state: AgentGraphState) -> str:
        if state.get("status") == "verified":
            return "analyze" if state.get("analysis_plan", {}).get("requires_multiple_steps") and analyze else "explain"
        return "reflect"

    def route_reflection(state: AgentGraphState) -> str:
        action = state.get("reflection", {}).get("action")
        if action == ReflectionAction.REGENERATE_SQL.value:
            return "generate"
        if action == ReflectionAction.RETRIEVE_MORE_SCHEMA.value:
            return "retrieve_semantics"
        if action == ReflectionAction.RETRY_EXECUTION.value:
            return "validate"
        return END

    def analyze_node(state: AgentGraphState) -> dict[str, Any]:
        try:
            return {"derived_analysis": parse_derived_analysis(analyze(state.get("result"), state.get("analysis_plan", {}))).model_dump(), "status": "analyzed"}
        except Exception as exc:
            raise _PostprocessError(exc) from exc

    def explain_node(state: AgentGraphState) -> dict[str, Any]:
        try:
            value = explain(state.get("result")) if explain else ResultExplanation(summary="查询完成", row_count=0)
            explanation = parse_result_explanation(value).model_dump()
            if state.get("derived_analysis", {}).get("summary"):
                explanation["summary"] = state["derived_analysis"]["summary"]
            return {"explanation": explanation, "status": "explained"}
        except Exception as exc:
            raise _PostprocessError(exc) from exc

    def materialize_node(state: AgentGraphState) -> dict[str, Any]:
        try:
            value = materialize(state.get("result"), state.get("explanation", {})) if materialize else ResultArtifact(name="查询结果", idempotency_key="query-result")
            return {"artifacts": [parse_result_artifact(value).model_dump()], "status": "completed"}
        except Exception as exc:
            raise _PostprocessError(exc) from exc

    builder = StateGraph(AgentGraphState)
    builder.add_node("resolve_semantics", node("resolve_semantics", resolve_semantics_node))
    builder.add_node("plan", node("plan", plan_node))
    builder.add_node("generate", async_node("generate", generate_node))
    builder.add_node("validate", node("validate", validate_node))
    builder.add_node("execute", node("execute", execute_node))
    builder.add_node("verify", async_node("verify", verify_node))
    builder.add_node("reflect", node("reflect", reflect_node))
    builder.add_node("retrieve_semantics", node("retrieve_semantics", retrieve_semantics_node))
    builder.add_node("analyze", node("analyze", analyze_node))
    builder.add_node("explain", node("explain", explain_node))
    builder.add_node("materialize", node("materialize", materialize_node))
    builder.add_edge(START, "resolve_semantics")
    builder.add_edge("resolve_semantics", "plan")
    builder.add_edge("plan", "generate")
    builder.add_conditional_edges("generate", route_generation, {"reflect": "reflect", "validate": "validate"})
    builder.add_conditional_edges("validate", route_validation, {"reflect": "reflect", "execute": "execute"})
    builder.add_conditional_edges("execute", route_execution, {"reflect": "reflect", "verify": "verify"})
    builder.add_conditional_edges("verify", route_verification, {"reflect": "reflect", "analyze": "analyze", "explain": "explain"})
    builder.add_conditional_edges("reflect", route_reflection, {"generate": "generate", "retrieve_semantics": "retrieve_semantics", "validate": "validate", END: END})
    builder.add_edge("retrieve_semantics", "generate")
    builder.add_edge("analyze", "explain")
    builder.add_edge("explain", "materialize")
    builder.add_edge("materialize", END)
    return LangGraphQueryAgent(builder.compile(), checkpoint)


def _apply_snapshot(state: QueryAgentState, snapshot: dict[str, Any]) -> None:
    """Copy persisted progress back to the caller without restoring raw results."""
    for name in (
        "sql", "error", "error_code", "repair_attempts", "max_repair_attempts", "status", "intent",
        "confidence", "needs_approval", "semantic_context", "analysis_plan",
        "verification", "reflection", "reflection_history", "reflection_budget",
        "derived_analysis", "explanation", "artifacts", "thread_id",
    ):
        if name in snapshot:
            setattr(state, name, snapshot[name])
