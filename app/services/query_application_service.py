"""Application orchestration for the synchronous and streaming query APIs.

The HTTP routes intentionally stay thin. This service owns the existing query
workflow so both transports share authorization, validation, approval,
execution, persistence, and audit behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException

from .. import approval
from ..agent import (
    AgentTerminalError,
    AnalysisPlan,
    ResultArtifact,
    ResultExplanation,
    VerificationResult,
    build_sql_generator,
)
from ..agent.contracts import merge_sql_params, parse_sql_plan
from ..agent.langchain_adapter import generate_sql as generate_sql_langchain
from ..agent.recovery_runtime import build_checkpoint_writer
from ..agent.state import QueryAgentState
from ..approval_store import create_approval
from ..audit import write_audit
from ..budget import cost_for_tokens, enforce_budget, estimate_query_cost, estimate_query_usage, settle_budget
from ..config import settings
from ..datasources import datasource_schema_details, get_datasource, list_datasources, role_can_use
from ..domain.data_gateway import GatewayError, GatewayQuery
from ..gateways.factory import build_gateway_registry
from ..guardrails import QueryError, validate_sql
from ..identity_store import save_query
from ..llm import ModelConfigurationError
from ..model_operations_runtime import get_model_operations_service
from ..policy import apply_policy, apply_row_filter, filter_schema, is_safe_scope_column, redact_rows
from ..query_admission_runtime import get_query_admission_service
from ..rate_limit import enforce_rate_limit
from ..retrieval import load_catalog, retrieve_context
from ..schemas.queries import QueryRequest
from ..schema_embedding_runtime import get_schema_embedding_scorer
from ..semantic_runtime import get_semantic_service
from ..task_runtime import get_task_service
from ..tool_replay_runtime import get_tool_replay_policy_service
from ..value_grounding_runtime import get_value_grounding_service
from .agent_query_service import AgentQueryService
from .agent_recovery_lease_service import AgentRecoveryLeaseService, RedisAgentRecoveryBackend
from .agent_trace_service import AgentTraceService
from app.agent_trace_runtime import get_agent_trace_exporter
from .analysis_service import infer_analysis_mode
from .chart_artifact_service import build_chart_artifact_definition
from .context_service import QueryContextService
from .conversation_service import build_task_conversation
from .data_gateway_service import DataGatewayService
from .datasource_service import DataSourceAccessService
from .prompt_injection_service import (
    PromptInjectionError,
    ensure_safe_generation_prompt,
    sanitize_untrusted_context,
)
from .quality_gate_service import (
    ApprovalGateRequired,
    anomaly_gate_reasons,
    evaluate_pre_execution,
    relationship_gate_reasons,
)
from .query_cost_service import evaluate_query_cost
from .query_admission_service import IdempotencyConflict
from .reflection_trace_service import ReflectionTraceService
from .query_service import QueryExecutionService
from .relationship_service import resolve_relationships
from .result_quality_service import ResultQualityService
from .result_reflection_coordinator import ResultReflectionCoordinator
from app.reflection_rollout_runtime import get_reflection_rollout_service
from .result_preview_service import build_result_preview
from .schema_retrieval_service import ambiguous_path_gate_reasons, retrieve_schema
from .semantic_critic_service import SemanticCriticService, build_openai_compatible_critic
from .task_context_service import build_branch_context, build_follow_up_context


def ground_column_values(datasource: dict, generation_schema: dict) -> dict:
    """Return bounded column-value grounding for the generator context.

    Only datasources that explicitly list ``value_columns`` participate, and
    only columns whose table survived the permission-filtered schema are
    sampled. Any failure degrades to an empty context.
    """
    if not settings.value_grounding_enabled:
        return {}
    service = get_value_grounding_service(datasource.get("backend"))
    if service is None:
        return {}
    try:
        return service.context(
            datasource_id=str(datasource.get("id", "")),
            allowed_tables=set(generation_schema),
            configured=datasource.get("value_columns") or [],
        )
    except Exception:
        return {}


@dataclass(frozen=True)
class QueryApplicationDependencies:
    """Patchable collaborators retained for route-level compatibility tests."""

    generate_sql: Callable[..., Any]
    create_query_graph: Callable[..., Any]
    describe_result: Callable[..., Any]
    get_skill_service: Callable[..., Any]
    get_user_grants: Callable[..., Any]


def _gateway_http_exception(exc: GatewayError) -> HTTPException:
    if "FORBIDDEN" in exc.code:
        status = 403
    elif exc.code.endswith("NOT_FOUND") or exc.code.endswith("TABLE"):
        status = 404
    elif "LIMIT" in exc.code:
        status = 422
    elif "UNSUPPORTED" in exc.code:
        status = 409
    else:
        status = 502
    return HTTPException(status_code=status, detail=exc.as_dict())


def _question_mentions_column(question: str, column: str) -> bool:
    name = str(column).strip().lower()
    text = str(question).lower()
    if not name:
        return False
    if re.fullmatch(r"[a-z0-9_]+", name):
        return re.search(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", text) is not None
    return name in text


def _branch_origin_payload(task) -> dict | None:
    if not getattr(task, "parent_task_id", ""):
        return None
    return {
        "parent_task_id": task.parent_task_id,
        "root_task_id": task.root_task_id,
        "source_trace_id": task.branch_source_trace_id,
        "label": task.branch_label,
        "depth": int(task.branch_depth),
    }


class QueryApplicationService:
    """Run one complete Query Pilot request independent of HTTP transport."""

    def __init__(self, dependencies: QueryApplicationDependencies):
        self.dependencies = dependencies

    async def execute(self, request: QueryRequest, identity: dict) -> dict:
        if not request.client_request_id:
            return await self._execute_once(request, identity)

        admission_service = get_query_admission_service()
        tenant_id = identity.get("tenant_id", "default")
        owner_id = str(identity.get("subject", ""))
        try:
            decision = admission_service.admit(
                request=request,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        except IdempotencyConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc).partition(":")[2]},
            ) from exc

        if not decision.created:
            return self._replay_admission(decision.admission, identity)

        try:
            result = await self._execute_once(
                request,
                identity,
                reserved_task_id=decision.admission.task_id,
            )
        except Exception as exc:
            admission_service.fail(
                decision.admission,
                error_code=self._error_code(exc),
            )
            raise

        error = result.get("error") if isinstance(result, dict) else None
        if isinstance(error, dict):
            admission_service.fail(
                decision.admission,
                error_code=str(error.get("code") or "E_QUERY_ERROR"),
            )
        else:
            admission_service.complete(decision.admission)
        if isinstance(result, dict):
            result.setdefault("task_id", decision.admission.task_id)
        return result

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                return str(detail.get("code") or "E_QUERY_HTTP")
            text = str(detail)
            code, separator, _message = text.partition(":")
            return code if separator and code.startswith("E_") else "E_QUERY_HTTP"
        code = getattr(exc, "code", "")
        return str(code or "E_QUERY_ERROR")

    @staticmethod
    def _replay_admission(admission, identity: dict) -> dict:
        task_service = get_task_service()
        tenant_id = identity.get("tenant_id", "default")
        owner_id = str(identity.get("subject", ""))
        try:
            detail = task_service.detail(
                task_id=admission.task_id,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
        except LookupError:
            if admission.status == "failed":
                return {
                    "task_id": admission.task_id,
                    "idempotent_replay": True,
                    "error": {
                        "code": admission.error_code or "E_QUERY_ERROR",
                        "message": "该查询请求已失败，请使用新的 client_request_id 重试",
                    },
                }
            return {
                "task_id": admission.task_id,
                "status": "running",
                "idempotent_replay": True,
            }

        conversation = build_task_conversation(detail)
        branch_origin = conversation.get("branch_origin")
        branch_projection = {"branch_origin": branch_origin} if branch_origin else {}
        turns = conversation.get("turns", [])
        latest = turns[-1] if turns else {}
        result = latest.get("result")
        if isinstance(result, dict):
            return {**result, **branch_projection, "idempotent_replay": True}
        if latest.get("status") == "approval_required":
            return {
                "trace_id": latest.get("trace_id", ""),
                "task_id": admission.task_id,
                "status": "approval_required",
                "approval_id": latest.get("approval_id", ""),
                "sql": latest.get("sql", ""),
                "message": latest.get("message", "查询需要人工确认"),
                **branch_projection,
                "idempotent_replay": True,
            }
        if admission.status == "failed" or latest.get("status") == "failed":
            return {
                "trace_id": latest.get("trace_id", ""),
                "task_id": admission.task_id,
                "sql": latest.get("sql", ""),
                **branch_projection,
                "idempotent_replay": True,
                "error": {
                    "code": admission.error_code or "E_QUERY_ERROR",
                    "message": "该查询请求已失败，请使用新的 client_request_id 重试",
                },
            }
        return {
            "task_id": admission.task_id,
            "status": "running",
            "task_status": conversation.get("status", admission.status),
            **branch_projection,
            "idempotent_replay": True,
        }

    async def _execute_once(
        self,
        request: QueryRequest,
        identity: dict,
        *,
        reserved_task_id: str | None = None,
    ) -> dict:
        generate_sql = self.dependencies.generate_sql
        create_query_graph = self.dependencies.create_query_graph
        describe_result = self.dependencies.describe_result
        get_skill_service = self.dependencies.get_skill_service
        get_user_grants = self.dependencies.get_user_grants
        replay_policies = get_tool_replay_policy_service()
        enforce_rate_limit(
            identity.get("subject", "anonymous"),
            limit=settings.rate_limit_per_minute,
            redis_url=settings.redis_url if settings.rate_limit_backend == "redis" else None,
        )
        estimated_input_tokens, estimated_output_tokens = estimate_query_usage(request.question, request.sql)
        estimated_cost = estimate_query_cost(request.question, request.sql)
        provider_usage: dict[str, int] = {}
        model_invoked = False
        def record_usage(value: dict[str, int]) -> None:
            provider_usage["input_tokens"] = provider_usage.get("input_tokens", 0) + max(0, int(value.get("input_tokens", 0) or 0))
            provider_usage["output_tokens"] = provider_usage.get("output_tokens", 0) + max(0, int(value.get("output_tokens", 0) or 0))
        def current_usage() -> tuple[int, int, float, str]:
            if not model_invoked:
                return 0, 0, 0.0, "none"
            input_tokens = provider_usage.get("input_tokens", estimated_input_tokens)
            output_tokens = provider_usage.get("output_tokens", estimated_output_tokens)
            source = "provider" if provider_usage else "estimate"
            return input_tokens, output_tokens, cost_for_tokens(input_tokens, output_tokens), source
        try:
            enforce_budget(estimated_cost)
        except ValueError as exc:
            code, _, message = str(exc).partition(":")
            return {"trace_id": str(uuid4()), "error": {"code": code, "message": message}, "estimated_input_tokens": estimated_input_tokens, "estimated_output_tokens": estimated_output_tokens, "estimated_cost_usd": estimated_cost}
        trace_id = str(uuid4())
        started = perf_counter()
        gateway_registry = build_gateway_registry(list_datasources())
        gateway_service = DataGatewayService(
            gateway_registry,
            can_use=lambda actor, descriptor: bool((configured := get_datasource(descriptor.id)) and role_can_use(actor, configured)),
            max_rows=settings.max_rows,
        )
        datasource_service = DataSourceAccessService(
            get_datasource=get_datasource,
            can_use=role_can_use,
            schema=lambda configured: {table: set(columns) for table, columns in gateway_service.schema(identity, configured["id"]).items()},
            execute=lambda configured, sql, params, max_rows, offset: (
                lambda result: (result.columns, result.rows, result.truncated)
            )(gateway_service.query(identity, configured["id"], GatewayQuery(sql=sql, params=params, max_rows=max_rows, offset=offset))),
        )
        task_service = get_task_service()
        tenant_id = identity.get("tenant_id", "default")
        runtime_model = get_model_operations_service().resolve_runtime(tenant_id)
        owner_id = str(identity.get("subject", ""))
        existing_detail = None
        task = None
        branch_source_detail = None
        branch_origin = None
        if request.follow_up and not request.task_id:
            raise HTTPException(status_code=422, detail="追问必须指定 task_id")
        if request.branch_from_task_id:
            try:
                branch_source_detail = task_service.detail(
                    task_id=request.branch_from_task_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="来源任务不存在") from exc
            source_task = branch_source_detail["task"]
            if source_task.status != "completed":
                raise HTTPException(
                    status_code=409,
                    detail=f"E_BRANCH_SOURCE_STATE:来源任务当前状态为 {source_task.status}，只有已完成任务可以创建分支",
                )
            if request.datasource and request.datasource != source_task.datasource_id:
                raise HTTPException(status_code=409, detail="E_BRANCH_DATASOURCE_MISMATCH:分析分支必须使用来源任务数据源")
            branch_depth = int(source_task.branch_depth or 0) + 1
            if branch_depth > settings.analysis_branch_max_depth:
                raise HTTPException(status_code=409, detail="E_BRANCH_DEPTH:分析分支超过允许的最大深度")
            children = task_service.list_branches(
                parent_task_id=source_task.id,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )
            if len(children) >= settings.analysis_branch_max_children:
                raise HTTPException(status_code=409, detail="E_BRANCH_CHILD_LIMIT:来源任务的分析分支数量已达上限")
            branch_origin = {
                "parent_task_id": source_task.id,
                "root_task_id": source_task.root_task_id or source_task.id,
                "source_trace_id": request.branch_from_trace_id,
                "label": request.branch_label.strip() or request.question.strip()[:120],
                "depth": branch_depth,
            }
            datasource_id = source_task.datasource_id
        elif request.task_id:
            try:
                existing_detail = task_service.detail(task_id=request.task_id, tenant_id=tenant_id, owner_id=owner_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="任务不存在") from exc
            task = existing_detail["task"]
            branch_origin = _branch_origin_payload(task)
            if branch_origin:
                try:
                    branch_source_detail = task_service.detail(
                        task_id=task.parent_task_id,
                        tenant_id=tenant_id,
                        owner_id=owner_id,
                    )
                except LookupError as exc:
                    raise HTTPException(status_code=409, detail="E_BRANCH_SOURCE_MISSING:分析分支来源任务不存在") from exc
            if request.follow_up:
                if task.status != "completed":
                    raise HTTPException(status_code=409, detail=f"E_TASK_FOLLOW_UP_STATE:任务当前状态为 {task.status}，只有已完成任务可以追问")
                if request.datasource and request.datasource != task.datasource_id:
                    raise HTTPException(status_code=409, detail="E_TASK_DATASOURCE_MISMATCH:追问必须使用原任务数据源")
                datasource_id = request.datasource or task.datasource_id
            else:
                datasource_id = request.datasource or settings.default_datasource
        else:
            datasource_id = request.datasource or settings.default_datasource
        try:
            datasource, current_schema = datasource_service.load_schema(identity, datasource_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="数据源不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="无权访问该数据源") from exc
        except GatewayError as exc:
            raise _gateway_http_exception(exc) from exc
        policy_identity = {**identity, "datasource": datasource["id"]}
        allowed_semantic_schema = filter_schema(current_schema, policy_identity)
        try:
            branch_context = (
                build_branch_context(
                    branch_source_detail,
                    source_trace_id=branch_origin["source_trace_id"],
                    question=request.question,
                    allowed_schema=allowed_semantic_schema,
                    compaction_threshold=settings.conversation_compaction_threshold_turns,
                    recent_turns=settings.conversation_recent_turns,
                    retrieval_turns=settings.conversation_retrieval_turns,
                    max_chars=settings.conversation_context_max_chars,
                )
                if branch_source_detail and branch_origin
                else None
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        follow_up_context = (
            build_follow_up_context(
                existing_detail,
                question=request.question,
                allowed_schema=allowed_semantic_schema,
                compaction_threshold=settings.conversation_compaction_threshold_turns,
                recent_turns=settings.conversation_recent_turns,
                retrieval_turns=settings.conversation_retrieval_turns,
                max_chars=settings.conversation_context_max_chars,
            )
            if request.follow_up and existing_detail
            else None
        )
        context_stats = (follow_up_context or {}).get("compaction", {})
        branch_context_stats = (branch_context or {}).get("compaction", {})
        try:
            if task is not None:
                if task.status not in {"draft", "failed", "clarification_required"}:
                    if not request.follow_up:
                        raise ValueError(f"E_TASK_ALREADY_RUNNING:任务当前状态为 {task.status}，不能重复执行")
                if request.follow_up and task.status != "completed":
                    raise ValueError(f"E_TASK_FOLLOW_UP_STATE:任务当前状态为 {task.status}，只有已完成任务可以追问")
                step_sequence = max((step.sequence for step in existing_detail["steps"]), default=-1) + 1
            else:
                task = task_service.create(
                    question=request.question,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    datasource_id=datasource["id"],
                    task_id=reserved_task_id,
                    title=(branch_origin or {}).get("label", ""),
                    parent_task_id=(branch_origin or {}).get("parent_task_id", ""),
                    root_task_id=(branch_origin or {}).get("root_task_id", ""),
                    branch_source_trace_id=(branch_origin or {}).get("source_trace_id", ""),
                    branch_label=(branch_origin or {}).get("label", ""),
                    branch_depth=int((branch_origin or {}).get("depth", 0)),
                )
                step_sequence = 0
                if branch_origin:
                    task_service.append_event(
                        task_id=task.id,
                        kind="task_branch_created",
                        payload={**branch_origin, "trace_id": trace_id},
                    )
                    task_service.append_event(
                        task_id=branch_origin["parent_task_id"],
                        kind="task_branch_spawned",
                        payload={
                            "branch_task_id": task.id,
                            "source_trace_id": branch_origin["source_trace_id"],
                            "label": branch_origin["label"],
                            "depth": branch_origin["depth"],
                        },
                    )
            if request.follow_up:
                task_service.begin_follow_up(task.id, tenant_id)
            else:
                task_service.transition(task.id, tenant_id, "planning")
            gateway_descriptor = gateway_registry.require(datasource["id"]).descriptor
            task_service.append_step(task_id=task.id, kind="understand_question", sequence=step_sequence, status="completed", input={"question": request.question, "follow_up": request.follow_up, "branch": bool(branch_origin)}, output={"gateway_id": gateway_descriptor.id, "datasource_id": datasource["id"], "dialect": gateway_descriptor.dialect, "trace_id": trace_id, "context_version": (follow_up_context or {}).get("version", ""), "context_turns": len((follow_up_context or {}).get("turns", [])), "context_source_turns": int(context_stats.get("source_turn_count", 0)), "compacted_turns": int(context_stats.get("compacted_turn_count", 0)), "retrieved_context_turns": int(context_stats.get("retrieved_turn_count", 0)), "filtered_context_turns": int(context_stats.get("filtered_turn_count", 0)), "context_chars": int(context_stats.get("serialized_chars", 0)), "branch_source_task_id": (branch_origin or {}).get("parent_task_id", ""), "branch_source_trace_id": (branch_origin or {}).get("source_trace_id", ""), "branch_depth": int((branch_origin or {}).get("depth", 0)), "branch_context_chars": int(branch_context_stats.get("serialized_chars", 0)), "model_provider_id": runtime_model.provider_id, "model": runtime_model.model, "prompt_version": runtime_model.prompt_version, "tool_replay": replay_policies.require("schema.inspect").as_dict()})
            step_sequence += 1
            task_service.transition(task.id, tenant_id, "running")
            task_service.append_event(task_id=task.id, kind="task_started", payload={"trace_id": trace_id})
            if request.follow_up:
                task_service.append_event(task_id=task.id, kind="task_follow_up", payload={"trace_id": trace_id, "turn_count": len((follow_up_context or {}).get("turns", [])), "compaction_version": (follow_up_context or {}).get("version", ""), "source_turn_count": int(context_stats.get("source_turn_count", 0)), "compacted_turns": int(context_stats.get("compacted_turn_count", 0)), "retrieved_turns": int(context_stats.get("retrieved_turn_count", 0)), "filtered_turns": int(context_stats.get("filtered_turn_count", 0)), "context_chars": int(context_stats.get("serialized_chars", 0))})
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        semantic_service = get_semantic_service()
        generation_schema = allowed_semantic_schema
        schema_retrieval_context: dict = {}
        raw_schema_details: dict = {}
        relationship_context: list[dict] = []
        try:
            raw_schema_details = datasource_schema_details(datasource)
            database_relationships = [relation if isinstance(relation, dict) else relation.__dict__ for relation in raw_schema_details.get("relationships", [])]
            catalog = load_catalog(settings.catalog_path)
            relationship_context = resolve_relationships(
                allowed_semantic_schema,
                database=database_relationships,
                manual=catalog.get("relationships", []),
                datasource_id=datasource["id"],
                tenant_id=tenant_id,
            )
        except Exception:
            relationship_context = []
        context_service = QueryContextService(
            load_catalog=load_catalog,
            retrieve=retrieve_context,
            catalog_path=settings.catalog_path,
            semantic_search=lambda question, limit: [
                {
                    "id": match.entry.id,
                    "logical_id": match.entry.logical_id,
                    "kind": match.entry.kind,
                    "name": match.entry.name,
                    "description": match.entry.description,
                    "definition": match.entry.definition,
                    "version": match.entry.version,
                    "match_score": match.score,
                    "match_reasons": list(match.reasons),
                }
                for match in semantic_service.search_matches(
                    question=question,
                    tenant_id=tenant_id,
                    datasource_id=datasource["id"],
                    allowed_schema=allowed_semantic_schema,
                    limit=limit,
                )
            ],
            value_search=lambda question, limit: semantic_service.search_value_matches(
                question=question,
                tenant_id=tenant_id,
                datasource_id=datasource["id"],
                allowed_schema=allowed_semantic_schema,
                limit=limit,
            ),
        )
        context = {"catalog_version": "unknown", "examples": [], "metrics": []}
        cost_estimate = None
        dialect = gateway_registry.require(datasource["id"]).descriptor.dialect
        sql = request.sql or ""

        def finish_failed_turn(error_code: str) -> None:
            if request.follow_up:
                task_service.restore_completed_after_follow_up(task.id, tenant_id, reason="follow_up_failed")
                task_service.append_event(task_id=task.id, kind="task_follow_up_failed", payload={"trace_id": trace_id, "error_code": error_code})
            else:
                task_service.transition(task.id, tenant_id, "failed")
                task_service.append_event(task_id=task.id, kind="task_failed", payload={"trace_id": trace_id, "error_code": error_code})

        try:
            trace_service = (
                AgentTraceService(
                    exporter=get_agent_trace_exporter(
                        settings.agent_trace_exporter,
                        settings.agent_trace_path,
                        settings.agent_trace_otlp_endpoint,
                        settings.agent_trace_otlp_headers,
                        settings.agent_trace_service_name,
                    )
                )
                if settings.agent_trace_enabled
                else None
            )

            def traced(stage: str, function: Callable) -> Callable:
                if trace_service is None:
                    return function
                return trace_service.wrap(
                    stage,
                    function,
                    trace_id=trace_id,
                    task_id=task.id,
                    tenant_id=tenant_id,
                )

            agent_service = AgentQueryService(
                provider=runtime_model.adapter,
                direct_generate=generate_sql,
                langchain_generate=generate_sql_langchain,
                build_langchain=build_sql_generator,
                api_key=runtime_model.api_key,
                base_url=runtime_model.base_url,
                model=runtime_model.model,
                timeout=settings.model_timeout_seconds,
                system_prompt=runtime_model.system_prompt,
                prompt_version=runtime_model.prompt_version,
            )

            async def generate_for_graph(question: str):
                nonlocal model_invoked
                if ("库存" in question and ("补货" in question or "低于" in question)
                        and "inventory" in allowed_semantic_schema and "products" in allowed_semantic_schema):
                    return "SELECT p.name, i.warehouse, i.available_qty, i.reorder_point FROM inventory i JOIN products p ON p.id = i.product_id WHERE i.available_qty < i.reorder_point"
                if ("待支付" in question or "待回款" in question) and "payments" in allowed_semantic_schema:
                    if "客户" in question and ("最高" in question or "前 20" in question or "前20" in question):
                        return "SELECT c.name, SUM(p.amount) AS pending_amount FROM payments p JOIN enterprise_orders o ON o.id = p.order_id JOIN enterprise_customers c ON c.id = o.customer_id WHERE p.status = 'pending' GROUP BY c.id, c.name ORDER BY pending_amount DESC LIMIT 20"
                    if "区域" in question:
                        return "SELECT o.region, SUM(p.amount) AS pending_amount FROM payments p JOIN enterprise_orders o ON o.id = p.order_id WHERE p.status = 'pending' GROUP BY o.region ORDER BY pending_amount DESC"
                ensure_safe_generation_prompt(question)
                model_invoked = True
                generated = await agent_service.generate(
                    question=question,
                    schema=generation_schema,
                    context=context,
                    dialect=dialect,
                    direct_kwargs={"model": runtime_model.model, "timeout": settings.model_timeout_seconds, "api_key": runtime_model.api_key, "base_url": runtime_model.base_url, "cache_ttl": settings.sql_cache_ttl_seconds, "cache_max_entries": settings.sql_cache_max_entries, "usage_callback": record_usage, "system_prompt": runtime_model.system_prompt, "prompt_version": runtime_model.prompt_version},
                )
                if provider_usage:
                    settle_budget(
                        estimated_cost,
                        cost_for_tokens(provider_usage.get("input_tokens", 0), provider_usage.get("output_tokens", 0)),
                    )
                plan = parse_sql_plan(generated)
                params.clear()
                params.update(merge_sql_params(request.params, plan.params, max_params=settings.max_params))
                return plan

            def resolve_semantics_for_graph(question: str) -> dict:
                nonlocal context, generation_schema, schema_retrieval_context
                context = context_service.retrieve(question)
                skill_route = traced(
                    "skill_resolve",
                    lambda: get_skill_service().route(
                        question,
                        identity=identity,
                        datasource_id=datasource["id"],
                        audit=False,
                    ),
                )()
                selected_skill = skill_route.get("selected_skill")
                if selected_skill:
                    deployment = selected_skill.get("deployment", {})
                    context["skill"] = {
                        "id": selected_skill["id"],
                        "name": selected_skill["name"],
                        "version": selected_skill["version"],
                        "state": deployment.get("state", "builtin"),
                        "description": selected_skill["description"],
                        "intents": selected_skill["intents"],
                        "workflow_steps": selected_skill["workflow_steps"],
                        "matched_terms": skill_route["evidence"]["matched_terms"],
                    }
                else:
                    context.pop("skill", None)
                descriptor_metadata = gateway_descriptor.metadata if isinstance(gateway_descriptor.metadata, dict) else {}
                context["execution_context"] = {
                    "current_date": datetime.now().astimezone().date().isoformat(),
                    "database_backend": gateway_descriptor.backend,
                    "database_dialect": dialect,
                    "database_version": str(descriptor_metadata.get("server_version") or descriptor_metadata.get("version") or "unknown"),
                }
                if follow_up_context:
                    context["follow_up"] = follow_up_context
                if branch_context:
                    context["branch_origin"] = branch_context
                retrieval = retrieve_schema(
                    question,
                    allowed_semantic_schema,
                    relationships=relationship_context,
                    semantics=context.get("semantics", []),
                    field_values=context.get("field_values", []),
                    schema_details=raw_schema_details,
                    enabled=settings.schema_retrieval_enabled,
                    table_threshold=settings.schema_retrieval_table_threshold,
                    max_tables=settings.schema_retrieval_max_tables,
                    max_columns_per_table=settings.schema_retrieval_max_columns_per_table,
                    max_hops=settings.schema_retrieval_max_hops,
                    embedding_scorer=get_schema_embedding_scorer(),
                )
                generation_schema = retrieval.schema
                schema_retrieval_context = retrieval.context_summary()
                column_values = ground_column_values(datasource, generation_schema)
                if column_values:
                    context["column_values"] = column_values
                context["relationships"] = retrieval.relationships
                context["join_paths"] = list(retrieval.join_paths)
                context["ambiguous_join_paths"] = list(retrieval.ambiguous_paths)
                authorized_examples = []
                for example in context.get("examples", []):
                    try:
                        apply_policy(
                            validate_sql(example.get("sql", ""), current_schema, settings.max_sql_length, dialect=datasource.get("backend", "sqlite"), max_params=settings.max_params),
                            policy_identity,
                            dialect=datasource.get("backend", "sqlite"),
                        )
                        authorized_examples.append(example)
                    except (QueryError, ValueError):
                        continue
                context["examples"] = authorized_examples
                if "admin" not in (identity.get("roles") or []):
                    context["metrics"] = []
                context, context_findings = sanitize_untrusted_context(
                    context,
                    source="retrieval_context",
                )
                if context_findings:
                    context["security_findings"] = context_findings[:20]
                return context

            def plan_for_graph(question: str, semantic_context: dict) -> AnalysisPlan:
                analysis_mode = request.analysis_mode or infer_analysis_mode(question)
                analysis_requested = analysis_mode != "none"
                return AnalysisPlan(
                    objective=question.strip() or "执行只读数据查询",
                    intent="data_query",
                    steps=["generate_sql", "validate_sql", "execute_query", "verify_result"] + (["analyze_result"] if analysis_requested else []),
                    requires_multiple_steps=analysis_requested,
                    analysis_mode=analysis_mode,
                )

            params = dict(request.params)
            backend = datasource.get("backend", "sqlite")
            query_service = QueryExecutionService(
                validate=lambda candidate: validate_sql(candidate, current_schema, settings.max_sql_length, dialect=backend, max_params=settings.max_params),
                execute=lambda candidate, query_params: datasource_service.execute(identity, datasource["id"], candidate, query_params, request.max_rows or settings.max_rows, request.offset),
                apply_policy=lambda candidate, actor: apply_policy(candidate, actor, dialect=backend),
            )
            critic_invoke = None
            if settings.agent_reflection_llm_enabled:
                base_critic_invoke = build_openai_compatible_critic(
                    api_key=runtime_model.api_key,
                    base_url=runtime_model.base_url,
                    model=runtime_model.model,
                    timeout_seconds=settings.agent_reflection_critic_timeout_seconds,
                    usage_callback=record_usage,
                )
                if base_critic_invoke is not None:
                    async def critic_invoke(prompt: str):
                        nonlocal model_invoked
                        model_invoked = True
                        return await base_critic_invoke(prompt)
            rollout_decision = get_reflection_rollout_service().resolve(
                tenant_id=str(identity.get("tenant_id", "default")),
                subject=str(identity.get("subject", "anonymous")),
                default_mode=settings.agent_reflection_mode,
                gate_report_path=settings.agent_reflection_release_gate_path,
            )
            reflection_coordinator = ResultReflectionCoordinator(
                quality=ResultQualityService(),
                critic=SemanticCriticService(
                    invoke=critic_invoke,
                    timeout_seconds=settings.agent_reflection_critic_timeout_seconds,
                    min_confidence=settings.agent_reflection_critic_min_confidence,
                    prompt_guard=ensure_safe_generation_prompt,
                ),
                critic_enabled=settings.agent_reflection_llm_enabled,
                requested_mode=rollout_decision.effective_mode,
                gate_report_path=settings.agent_reflection_release_gate_path,
            )
            last_executed_sql = ""

            def execute_for_graph(candidate: str, query_params: dict):
                nonlocal last_executed_sql
                last_executed_sql = candidate
                output = query_service.execute_validated(candidate, {**query_params, **params})
                return output[0], redact_rows(output[0], output[1], policy_identity), output[2]

            def expected_result_columns() -> tuple[list[str], list[str]]:
                question_text = request.question.lower()
                dimensions = sorted({
                    str(column).lower()
                    for columns in allowed_semantic_schema.values()
                    for column in columns
                    if _question_mentions_column(question_text, str(column))
                })
                metric_aliases = []
                for metric in context.get("metrics", []):
                    if not isinstance(metric, dict) or str(metric.get("name", "")).lower() not in question_text:
                        continue
                    alias = str(metric.get("result_alias", "")).strip().lower()
                    if alias:
                        metric_aliases.append(alias)
                return dimensions, sorted(set(metric_aliases))

            async def verify_for_graph(output) -> VerificationResult:
                structurally_valid = (
                    isinstance(output, tuple)
                    and len(output) == 3
                    and isinstance(output[0], list)
                    and isinstance(output[1], list)
                    and all(isinstance(row, (list, tuple)) and len(row) == len(output[0]) for row in output[1])
                )
                if not structurally_valid:
                    return VerificationResult(
                        accepted=False,
                        reason="查询结果列定义与数据行结构不一致",
                        retryable=False,
                        source="structural",
                        error_code="E_RESULT_SHAPE",
                    )
                expected_dimensions, expected_metrics = expected_result_columns()
                explain_summary = {
                    key: getattr(cost_estimate, key, None)
                    for key in ("backend", "supported", "estimated_rows", "estimated_cost", "full_scan", "index_used", "reason")
                    if cost_estimate is not None and getattr(cost_estimate, key, None) is not None
                }
                return await reflection_coordinator.verify(
                    question=request.question,
                    sql=last_executed_sql,
                    columns=output[0],
                    rows=output[1],
                    truncated=bool(output[2]),
                    authorized_schema=allowed_semantic_schema,
                    relationships=relationship_context,
                    schema_details=raw_schema_details,
                    expected_dimensions=expected_dimensions,
                    expected_metrics=expected_metrics,
                    explain_summary=explain_summary,
                    provided_sql=bool(request.sql),
                )

            def validate_for_graph(candidate: str) -> str:
                candidate = query_service.validate_candidate(candidate, policy_identity)
                if str(identity.get("subject", "")).isdigit() and "admin" not in (identity.get("roles") or []):
                    known_columns = {column.lower() for cols in current_schema.values() for column in cols}
                    grouped: dict[str, list[str]] = {}
                    for grant in get_user_grants(int(identity["subject"])):
                        if grant.get("datasource_id") not in (datasource["id"], "default"):
                            continue
                        grouped.setdefault(grant.get("scope_column", ""), []).append(grant.get("scope_value"))
                    for scope_column, values in grouped.items():
                        if not is_safe_scope_column(scope_column) or scope_column.lower() not in known_columns:
                            continue
                        nonlocal_params = apply_row_filter(candidate, scope_column, values)
                        candidate, extra = nonlocal_params
                        params.update(extra)
                return candidate

            def queue_approval(*, approval_sql: str, reasons: tuple[str, ...], trigger: str, analysis_mode: str = "none", confidence: float | None = None):
                approval_request = approval.create(
                    identity=identity,
                    question=request.question,
                    sql=approval_sql,
                    params=params,
                    datasource_id=datasource["id"],
                    task_id=task.id,
                    ttl_seconds=settings.agent_approval_ttl_seconds,
                    reasons=reasons,
                    trigger=trigger,
                    analysis_mode=analysis_mode,
                )
                if settings.auth_enabled:
                    approval_request = create_approval({**approval_request, "params": params, "tenant_id": identity.get("tenant_id"), "requester_id": identity.get("subject"), "ttl_seconds": settings.agent_approval_ttl_seconds})
                usage_input, usage_output, usage_cost, usage_source = current_usage()
                write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql=approval_sql, duration_ms=int((perf_counter() - started) * 1000), model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context.get("catalog_version", "unknown"), input_tokens=usage_input, output_tokens=usage_output, cost_usd=usage_cost, usage_source=usage_source, event="approval_created", actor_id=str(identity.get("subject")), approval_id=approval_request["id"])
                task_service.append_step(task_id=task.id, kind="approval", sequence=step_sequence, status="pending", output={"approval_id": approval_request["id"], "sql": approval_sql, "trigger": trigger, "reasons": list(reasons), "confidence": confidence, "analysis_mode": analysis_mode, "tool_replay": replay_policies.require("approval.create").as_dict()})
                task_service.transition(task.id, tenant_id, "approval_required")
                task_service.append_event(task_id=task.id, kind="approval_required", payload={"trace_id": trace_id, "approval_id": approval_request["id"], "trigger": trigger, "reasons": list(reasons)})
                response = {"trace_id": trace_id, "task_id": task.id, "status": "approval_required", "approval_id": approval_request["id"], "sql": approval_sql, "approval_trigger": trigger, "approval_reasons": list(reasons), "message": "质量门禁要求人工确认" if trigger == "automatic" else "查询需要人工确认"}
                if branch_origin:
                    response["branch_origin"] = branch_origin
                return response

            reflection_trace = ReflectionTraceService(task_service)

            def persist_reflection_records(reflections: list[dict], *, final_status: str, mode: str = "enforced", source: str = "deterministic") -> None:
                nonlocal step_sequence
                if not reflections:
                    return
                start_attempt = 1
                step_sequence = reflection_trace.persist(
                    task_id=task.id,
                    start_sequence=step_sequence,
                    reflections=reflections,
                    sql=sql or last_executed_sql,
                    model=runtime_model.model,
                    prompt_version=runtime_model.prompt_version,
                    final_status=final_status,
                    mode=mode,
                    source=source,
                )
                for offset, reflection in enumerate(reflections):
                    write_audit(
                        settings.audit_log_path,
                        trace_id=trace_id,
                        task_id=task.id,
                        tenant_id=tenant_id,
                        sql=sql or last_executed_sql,
                        duration_ms=0,
                        error_code=(reflection.get("reason_codes") or [None])[0],
                        model=runtime_model.model,
                        prompt_version=runtime_model.prompt_version,
                        dataset_version=context.get("catalog_version", "unknown"),
                        event="reflection",
                        actor_id=str(identity.get("subject")),
                        reflection_failure_type=reflection.get("failure_type"),
                        reflection_action=reflection.get("action"),
                        reflection_attempt=start_attempt + offset,
                        reflection_reason_codes=reflection.get("reason_codes") or [],
                        reflection_final_status=final_status,
                    )
                if trace_service is not None:
                    trace_service.record(
                        stage="reflect",
                        status="ok",
                        duration_ms=0,
                        trace_id=trace_id,
                        task_id=task.id,
                        tenant_id=tenant_id,
                    )

            def require_quality_approval(candidate: str, confidence: float, needs_approval: bool, analysis_plan: dict) -> None:
                nonlocal cost_estimate
                decision = evaluate_pre_execution(
                    candidate,
                    confidence=confidence,
                    model_requires_approval=needs_approval,
                    requested_rows=request.max_rows or settings.max_rows,
                    low_confidence_threshold=settings.agent_low_confidence_threshold,
                    high_risk_row_threshold=settings.agent_high_risk_row_threshold,
                )
                if decision.required:
                    raise ApprovalGateRequired(
                        sql=candidate,
                        reasons=decision.reasons,
                        confidence=decision.confidence,
                        analysis_mode=str(analysis_plan.get("analysis_mode", "none")),
                    )
                if settings.relationship_approval_required:
                    relation_reasons = relationship_gate_reasons(candidate, relationship_context, min_confidence=settings.relationship_min_confidence, require_approved=True)
                    if relation_reasons:
                        raise ApprovalGateRequired(sql=candidate, reasons=relation_reasons, confidence=decision.confidence, analysis_mode=str(analysis_plan.get("analysis_mode", "none")))
                    ambiguity_reasons = ambiguous_path_gate_reasons(candidate, schema_retrieval_context.get("ambiguous_paths", []))
                    if ambiguity_reasons:
                        raise ApprovalGateRequired(sql=candidate, reasons=ambiguity_reasons, confidence=decision.confidence, analysis_mode=str(analysis_plan.get("analysis_mode", "none")))
                if settings.query_cost_guard_enabled:
                    try:
                        estimate = gateway_service.estimate_cost(
                            identity,
                            datasource["id"],
                            GatewayQuery(sql=candidate, params=params, max_rows=request.max_rows or settings.max_rows, offset=request.offset),
                        )
                        cost_estimate = estimate
                    except GatewayError as exc:
                        raise QueryError("E_QUERY_COST", "查询成本评估失败，请稍后重试") from exc
                    cost_decision = evaluate_query_cost(
                        estimate,
                        max_estimated_rows=settings.query_cost_max_estimated_rows,
                        max_estimated_cost=settings.query_cost_max_estimated_cost,
                        require_explain=settings.query_cost_require_explain,
                    )
                    if not cost_decision.allowed:
                        raise ApprovalGateRequired(
                            sql=candidate,
                            reasons=tuple(f"query_cost:{reason}" for reason in cost_decision.reasons),
                            confidence=decision.confidence,
                            analysis_mode=str(analysis_plan.get("analysis_mode", "none")),
                        )

            if request.require_approval:
                context = resolve_semantics_for_graph(request.question)
                sql = validate_for_graph(request.sql or await generate_for_graph(request.question))
                return queue_approval(approval_sql=sql, reasons=("user_requested",), trigger="manual", analysis_mode=str(request.analysis_mode or infer_analysis_mode(request.question)))

            checkpoint = None
            if settings.agent_checkpoint_enabled:
                recovery_service = AgentRecoveryLeaseService(
                    backend=RedisAgentRecoveryBackend(
                        redis_url=settings.redis_url,
                        checkpoint_ttl_seconds=settings.agent_checkpoint_ttl_seconds,
                    ),
                    lease_seconds=settings.agent_recovery_lease_seconds,
                )
                checkpoint = build_checkpoint_writer(
                    service=recovery_service,
                    task_id=task.id,
                    tenant_id=tenant_id,
                    execution_id=trace_id,
                    holder_id=f"api:{trace_id}",
                )
            artifact_key = f"{task.id}:table:query-result:{trace_id}" if request.follow_up else f"{task.id}:table:query-result"
            graph = create_query_graph(
                runtime=settings.agent_runtime,
                generate=traced("sql_generate", generate_for_graph),
                validate=traced("validate", validate_for_graph),
                execute=traced("execute", execute_for_graph),
                resolve_semantics=traced("schema_retrieve", resolve_semantics_for_graph),
                plan=traced("plan", plan_for_graph),
                verify=traced("verify", verify_for_graph),
                analyze=traced("analyze", describe_result),
                explain=traced("explain", lambda output: ResultExplanation(
                    summary=f"查询完成，共返回 {len(output[1])} 行。" + (" 结果已截断。" if output[2] else ""),
                    row_count=len(output[1]),
                    truncated=output[2],
                    method="deterministic",
                )),
                materialize=traced("materialize", lambda output, _explanation: ResultArtifact(
                    name="查询结果",
                    idempotency_key=artifact_key,
                    metadata=build_result_preview(
                        output[0],
                        output[1],
                        source_truncated=output[2],
                        extra_metadata={"data_scope": "redacted"},
                    ),
                )),
                pre_execute=traced("explain", require_quality_approval),
                max_repairs=settings.agent_max_repairs,
                max_schema_expansions=settings.agent_max_schema_expansions,
                max_execution_retries=settings.agent_max_execution_retries,
                max_reflection_steps=settings.agent_max_reflection_steps,
                checkpoint=checkpoint,
            )
            agent_state = QueryAgentState(request.question, sql=sql, params=params, thread_id=trace_id)
            graph_result = await graph.ainvoke(agent_state)
            sql = graph_result["state"].sql
            params = graph_result["state"].params
            columns, rows, truncated = graph_result["result"]
        except ApprovalGateRequired as exc:
            sql = exc.sql
            return queue_approval(approval_sql=exc.sql, reasons=exc.reasons, trigger="automatic", analysis_mode=exc.analysis_mode, confidence=exc.confidence)
        except AgentTerminalError as exc:
            sql = getattr(locals().get("agent_state"), "sql", sql) or sql
            history = list(getattr(locals().get("agent_state"), "reflection_history", []) or [])
            persist_reflection_records(history, final_status=exc.status)
            if exc.status == "approval_required":
                reasons = tuple((history[-1].get("reason_codes") if history else None) or [exc.code])
                return queue_approval(
                    approval_sql=sql,
                    reasons=reasons,
                    trigger="automatic",
                    analysis_mode=str(getattr(locals().get("agent_state"), "analysis_plan", {}).get("analysis_mode", "none")),
                    confidence=(history[-1].get("confidence") if history else None),
                )
            if exc.status == "clarification_required":
                if request.follow_up:
                    task_service.restore_completed_after_follow_up(task.id, tenant_id, reason="follow_up_clarification_required")
                else:
                    task_service.transition(task.id, tenant_id, "clarification_required")
                task_service.append_event(task_id=task.id, kind="clarification_required", payload={"trace_id": trace_id, "reason_codes": (history[-1].get("reason_codes") if history else [exc.code])})
                return {"trace_id": trace_id, "task_id": task.id, "status": "clarification_required", "sql": sql, "error": {"code": exc.code, "message": exc.message}}
            task_service.append_step(task_id=task.id, kind="query", sequence=step_sequence, status="failed", output={"sql": sql, "trace_id": trace_id, "tool_replay": replay_policies.require("query.execute").as_dict()}, error=exc.message)
            finish_failed_turn(exc.code)
            usage_input, usage_output, usage_cost, usage_source = current_usage()
            write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql=sql, duration_ms=int((perf_counter() - started) * 1000), error_code=exc.code, model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context.get("catalog_version", "unknown"), input_tokens=usage_input, output_tokens=usage_output, cost_usd=usage_cost, usage_source=usage_source, actor_id=str(identity.get("subject")))
            return {"trace_id": trace_id, "task_id": task.id, "error": {"code": exc.code, "message": exc.message}, "sql": sql}
        except ModelConfigurationError as exc:
            task_service.append_step(task_id=task.id, kind="generate_sql", sequence=step_sequence, status="failed", output={"trace_id": trace_id}, error=str(exc))
            finish_failed_turn(exc.code)
            usage_input, usage_output, usage_cost, usage_source = current_usage()
            write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql="", duration_ms=int((perf_counter() - started) * 1000), error_code=exc.code, model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context["catalog_version"], input_tokens=usage_input, output_tokens=usage_output, cost_usd=usage_cost, usage_source=usage_source, actor_id=str(identity.get("subject")))
            return {"trace_id": trace_id, "task_id": task.id, "error": {"code": exc.code, "message": str(exc)}, "sql": ""}
        except PromptInjectionError as exc:
            task_service.append_step(task_id=task.id, kind="generate_sql", sequence=step_sequence, status="failed", output={"trace_id": trace_id, "prompt_fingerprint": exc.decision.fingerprint, "reason_codes": list(exc.decision.reasons)}, error=str(exc))
            finish_failed_turn(exc.code)
            write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql="", duration_ms=int((perf_counter() - started) * 1000), error_code=exc.code, model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context["catalog_version"], event="prompt_injection_blocked", actor_id=str(identity.get("subject")))
            return {"trace_id": trace_id, "task_id": task.id, "error": {"code": exc.code, "message": str(exc)}, "sql": ""}
        except QueryError as exc:
            task_service.append_step(task_id=task.id, kind="query", sequence=step_sequence, status="failed", output={"sql": sql, "trace_id": trace_id, "tool_replay": replay_policies.require("query.execute").as_dict()}, error=str(exc))
            finish_failed_turn(exc.code)
            usage_input, usage_output, usage_cost, usage_source = current_usage()
            write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql=sql, duration_ms=int((perf_counter() - started) * 1000), error_code=exc.code, model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context["catalog_version"], input_tokens=usage_input, output_tokens=usage_output, cost_usd=usage_cost, usage_source=usage_source, actor_id=str(identity.get("subject")))
            return {"trace_id": trace_id, "task_id": task.id, "error": {"code": exc.code, "message": str(exc)}, "sql": sql}
        except ValueError as exc:
            code, _, message = str(exc).partition(":")
            task_service.append_step(task_id=task.id, kind="query", sequence=step_sequence, status="failed", output={"sql": sql, "trace_id": trace_id, "tool_replay": replay_policies.require("query.execute").as_dict()}, error=message or str(exc))
            finish_failed_turn(code or "E_QUERY_ERROR")
            usage_input, usage_output, usage_cost, usage_source = current_usage()
            write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql=sql, duration_ms=int((perf_counter() - started) * 1000), error_code=code or "E_QUERY_ERROR", model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context["catalog_version"], input_tokens=usage_input, output_tokens=usage_output, cost_usd=usage_cost, usage_source=usage_source, actor_id=str(identity.get("subject")))
            return {"trace_id": trace_id, "task_id": task.id, "error": {"code": code or "E_QUERY_ERROR", "message": message or "查询执行失败"}, "sql": sql}
        except Exception:
            task_service.append_step(task_id=task.id, kind="postprocess", sequence=step_sequence, status="failed", output={"sql": sql, "trace_id": trace_id}, error="Unexpected postprocessing failure")
            finish_failed_turn("E_AGENT_POSTPROCESS")
            usage_input, usage_output, usage_cost, usage_source = current_usage()
            write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql=sql, duration_ms=int((perf_counter() - started) * 1000), error_code="E_AGENT_POSTPROCESS", model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context["catalog_version"], input_tokens=usage_input, output_tokens=usage_output, cost_usd=usage_cost, usage_source=usage_source, actor_id=str(identity.get("subject")))
            return {"trace_id": trace_id, "task_id": task.id, "error": {"code": "E_AGENT_POSTPROCESS", "message": "查询后处理失败，请联系管理员并提供 trace_id"}, "sql": sql}
        resolved_context = graph_result["state"].semantic_context
        semantic_refs = [{"id": item["id"], "logical_id": item["logical_id"], "version": item["version"], "kind": item["kind"], "name": item["name"], "definition": item["definition"], "match_score": item.get("match_score", 0), "match_reasons": item.get("match_reasons", [])} for item in resolved_context.get("semantics", [])]
        field_value_refs = [{key: item.get(key) for key in ("id", "logical_id", "version", "name", "table", "column", "score")} for item in resolved_context.get("field_values", [])]
        expansion = resolved_context.get("query_expansion", {})
        expansion_summary = {"mode": expansion.get("mode", "none"), "evidence_count": expansion.get("evidence_count", 0), "term_count": len(expansion.get("terms", []))}
        task_service.append_step(task_id=task.id, kind="resolve_semantics", sequence=step_sequence, status="completed", output={"catalog_version": resolved_context.get("catalog_version", "unknown"), "examples": len(resolved_context.get("examples", [])), "metrics": len(resolved_context.get("metrics", [])), "semantic_entries": len(semantic_refs), "semantic_versions": {item["logical_id"]: item["version"] for item in semantic_refs}, "semantic_refs": semantic_refs, "field_value_matches": len(field_value_refs), "field_value_refs": field_value_refs, "query_expansion": expansion_summary, "skill": resolved_context.get("skill"), "execution_context": resolved_context.get("execution_context", {}), "schema_retrieval": schema_retrieval_context})
        step_sequence += 1
        task_service.append_step(task_id=task.id, kind="plan_analysis", sequence=step_sequence, status="completed", output=graph_result["state"].analysis_plan)
        step_sequence += 1
        persist_reflection_records(
            list(graph_result["state"].reflection_history),
            final_status=graph_result["state"].status,
        )
        suggested_reflection = graph_result["state"].verification.get("suggested_reflection")
        if suggested_reflection:
            persist_reflection_records(
                [suggested_reflection],
                final_status="shadow_observed",
                mode="shadow",
                source=str(graph_result["state"].verification.get("source", "deterministic")),
            )
        task_service.append_step(task_id=task.id, kind="query", sequence=step_sequence, status="completed", output={"sql": sql, "columns": columns, "row_count": len(rows), "trace_id": trace_id, "tool_replay": replay_policies.require("query.execute").as_dict()}, duration_ms=int((perf_counter() - started) * 1000), retry_count=graph_result["state"].repair_attempts)
        step_sequence += 1
        task_service.append_step(task_id=task.id, kind="verify_result", sequence=step_sequence, status="completed", output={"row_count": len(rows), "truncated": truncated, **graph_result["state"].verification})
        step_sequence += 1
        post_gate_reasons = anomaly_gate_reasons(graph_result["state"].derived_analysis)
        if post_gate_reasons:
            return queue_approval(approval_sql=sql, reasons=post_gate_reasons, trigger="automatic", analysis_mode=str(graph_result["state"].analysis_plan.get("analysis_mode", "none")))
        task_service.transition(task.id, tenant_id, "verifying")
        if graph_result["state"].derived_analysis:
            task_service.append_step(task_id=task.id, kind="analyze_result", sequence=step_sequence, status="completed", output=graph_result["state"].derived_analysis)
            step_sequence += 1
        task_service.append_step(task_id=task.id, kind="explain_result", sequence=step_sequence, status="completed", output=graph_result["state"].explanation)
        step_sequence += 1
        artifact = graph_result["state"].artifacts[0]
        task_service.ensure_artifact(task_id=task.id, kind=artifact["kind"], name=artifact["name"], idempotency_key=artifact["idempotency_key"], metadata=artifact["metadata"])
        artifact_keys = [artifact["idempotency_key"]]
        chart_artifact = build_chart_artifact_definition(
            artifact["metadata"],
            source_idempotency_key=artifact["idempotency_key"],
            chart_idempotency_key=f"{artifact['idempotency_key']}:chart",
        )
        if chart_artifact:
            task_service.ensure_artifact(task_id=task.id, kind=chart_artifact["kind"], name=chart_artifact["name"], idempotency_key=chart_artifact["idempotency_key"], metadata=chart_artifact["metadata"])
            artifact_keys.append(chart_artifact["idempotency_key"])
        task_service.append_step(task_id=task.id, kind="materialize_artifacts", sequence=step_sequence, status="completed", output={"count": len(artifact_keys), "idempotency_key": artifact["idempotency_key"], "idempotency_keys": artifact_keys, "tool_replay": replay_policies.require("result.materialize").as_dict()})
        task_service.transition(task.id, tenant_id, "completed")
        task_service.append_event(task_id=task.id, kind="task_completed", payload={"trace_id": trace_id, "row_count": len(rows)})
        if trace_service is not None:
            trace_service.record(
                stage="request",
                status="ok",
                duration_ms=int((perf_counter() - started) * 1000),
                trace_id=trace_id,
                task_id=task.id,
                tenant_id=tenant_id,
            )
        actual_input_tokens, actual_output_tokens, actual_cost, usage_source = current_usage()
        write_audit(settings.audit_log_path, trace_id=trace_id, task_id=task.id, tenant_id=tenant_id, sql=sql, duration_ms=int((perf_counter() - started) * 1000), row_count=len(rows), truncated=truncated, large_result=len(rows) >= settings.large_result_row_threshold, model=runtime_model.model, prompt_version=runtime_model.prompt_version, dataset_version=context["catalog_version"], input_tokens=actual_input_tokens, output_tokens=actual_output_tokens, cost_usd=actual_cost, usage_source=usage_source, actor_id=str(identity.get("subject")), cost_backend=getattr(cost_estimate, "backend", None), cost_supported=getattr(cost_estimate, "supported", None), estimated_rows=getattr(cost_estimate, "estimated_rows", None), estimated_cost=getattr(cost_estimate, "estimated_cost", None), full_scan=getattr(cost_estimate, "full_scan", None), index_used=getattr(cost_estimate, "index_used", None))
        response = {"trace_id": trace_id, "task_id": task.id, "question": request.question, "follow_up": request.follow_up, "sql": sql, "columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated, "answer": graph_result["state"].explanation["summary"], "analysis": graph_result["state"].derived_analysis or None, "skill": resolved_context.get("skill")}
        if branch_origin:
            response["branch_origin"] = branch_origin
        if str(identity.get("subject", "")).isdigit() and request.question.strip() not in {"查询", "查询数据", "测试查询"}:
            try:
                session_id = save_query(int(identity["subject"]), identity["tenant_id"], request.question, sql, {**response, "rows": rows[:100]})
                response["session_id"] = session_id
            except Exception: pass
        return response


__all__ = ["QueryApplicationDependencies", "QueryApplicationService"]

