"""Administrator review, export and regression endpoints for the badcase loop."""

from fastapi import APIRouter, Depends, HTTPException

from app.agent import build_sql_generator
from app.agent.langchain_adapter import generate_sql as generate_sql_langchain
from app.api.dependencies import require_manage_access
from app.audit import write_audit
from app.config import settings
from app.datasources import datasource_schema, execute_datasource, get_datasource
from app.feedback_regression import evaluate_feedback_regression
from app.llm import generate_sql
from app.model_operations_runtime import get_model_operations_service
from app.schemas.feedback import TaskFeedbackReviewRequest
from app.services.agent_query_service import AgentQueryService
from app.services.feedback_service import FeedbackService
from app.task_runtime import get_task_service


router = APIRouter(prefix="/api/v1/quality/admin", tags=["quality-admin"])


@router.post("/tasks/{task_id}/feedback/{feedback_id}/review")
def review_feedback(
    task_id: str,
    feedback_id: str,
    request: TaskFeedbackReviewRequest,
    identity: dict = Depends(require_manage_access),
):
    tenant_id = identity.get("tenant_id", "default")
    reviewer_id = str(identity.get("subject", ""))
    try:
        result = FeedbackService(get_task_service()).review(
            task_id=task_id,
            tenant_id=tenant_id,
            feedback_id=feedback_id,
            reviewer_id=reviewer_id,
            decision=request.decision,
            dataset_version=request.dataset_version,
            note=request.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="反馈不存在") from exc
    except ValueError as exc:
        code, _, message = str(exc).partition(":")
        raise HTTPException(status_code=409, detail={"code": code, "message": message or str(exc)}) from exc
    write_audit(
        settings.audit_log_path,
        trace_id=feedback_id,
        task_id=task_id,
        tenant_id=tenant_id,
        sql="",
        duration_ms=0,
        event=f"feedback_{request.decision}",
        actor_id=reviewer_id,
        dataset_version=request.dataset_version or None,
    )
    return result


@router.get("/evaluation-datasets/{dataset_version}")
def get_feedback_evaluation_dataset(
    dataset_version: str,
    identity: dict = Depends(require_manage_access),
):
    if not dataset_version or len(dataset_version) > 128:
        raise HTTPException(status_code=422, detail="dataset_version 无效")
    return FeedbackService(get_task_service()).approved_dataset(
        tenant_id=identity.get("tenant_id", "default"),
        dataset_version=dataset_version,
    )


@router.post("/evaluation-datasets/{dataset_version}/regressions")
async def run_feedback_regression(
    dataset_version: str,
    identity: dict = Depends(require_manage_access),
):
    if not dataset_version or len(dataset_version) > 128:
        raise HTTPException(status_code=422, detail="dataset_version 无效")
    tenant_id = identity.get("tenant_id", "default")
    dataset = FeedbackService(get_task_service()).approved_dataset(
        tenant_id=tenant_id,
        dataset_version=dataset_version,
    )
    runtime = get_model_operations_service().resolve_runtime(tenant_id)
    agent_service = AgentQueryService(
        provider=runtime.adapter,
        direct_generate=generate_sql,
        langchain_generate=generate_sql_langchain,
        build_langchain=build_sql_generator,
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        model=runtime.model,
        timeout=settings.model_timeout_seconds,
        system_prompt=runtime.system_prompt,
        prompt_version=runtime.prompt_version,
    )

    async def generate_current(**kwargs):
        return await agent_service.generate(
            question=kwargs["question"],
            schema=kwargs["schema"],
            context=kwargs.get("context"),
            dialect=kwargs["dialect"],
            direct_kwargs={
                "model": runtime.model,
                "timeout": settings.model_timeout_seconds,
                "api_key": runtime.api_key,
                "base_url": runtime.base_url,
                "cache_ttl": settings.sql_cache_ttl_seconds,
                "cache_max_entries": settings.sql_cache_max_entries,
                "system_prompt": runtime.system_prompt,
                "prompt_version": runtime.prompt_version,
            },
        )

    def resolve_datasource(case: dict) -> dict:
        datasource = get_datasource(str(case.get("datasource_id") or ""))
        if datasource is None:
            raise ValueError("E_DATASOURCE_NOT_FOUND")
        return datasource

    report = await evaluate_feedback_regression(
        dataset=dataset,
        generate=generate_current if runtime.api_key else None,
        schema_for=lambda case: datasource_schema(resolve_datasource(case)),
        dialect_for=lambda case: str(resolve_datasource(case).get("backend") or "sqlite").lower(),
        execute=lambda case, sql, params: execute_datasource(
            resolve_datasource(case),
            sql,
            params,
            settings.max_rows,
        ),
        model={
            "provider": runtime.provider_name,
            "model": runtime.model,
            "prompt_version": runtime.prompt_version,
        },
    )
    write_audit(
        settings.audit_log_path,
        trace_id=f"feedback-regression:{dataset_version}",
        tenant_id=tenant_id,
        sql="",
        duration_ms=0,
        event="feedback_regression_completed",
        actor_id=str(identity.get("subject", "")),
        dataset_version=dataset_version,
        error_code="" if report["gate"]["passed"] else report["gate"]["reason_codes"][0],
    )
    return report


__all__ = ["router"]
