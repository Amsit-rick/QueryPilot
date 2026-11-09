"""User-scoped proactive analysis management API."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import require_query_access
from app.config import settings
from app.datasources import get_datasource, role_can_use
from app.domain.proactive_analysis import ProactiveAnalysisSpec
from app.proactive_analysis_runtime import get_proactive_analysis_service
from app.schemas.proactive_analysis import ProactiveAnalysisCreateRequest


router = APIRouter(prefix="/api/v1/proactive-analysis", tags=["proactive-analysis"])


def _plan_payload(plan) -> dict:
    value = asdict(plan)
    value["spec"] = plan.spec.model_dump(mode="json")
    for key in ("next_run_at", "last_run_at", "lease_expires_at", "created_at", "updated_at"):
        value[key] = value[key].isoformat() if value.get(key) else None
    value["budget_date"] = value["budget_date"].isoformat() if value.get("budget_date") else None
    value.pop("lease_token", None)
    value.pop("lease_expires_at", None)
    return value


def _run_payload(run) -> dict:
    value = asdict(run)
    for key in ("scheduled_for", "started_at", "completed_at"):
        value[key] = value[key].isoformat()
    return value


def _call(call):
    try:
        return _plan_payload(call())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "STATE" in str(exc) else 422, detail=str(exc)) from exc


@router.get("/status")
def proactive_status(identity: dict = Depends(require_query_access)):
    return {
        "enabled": settings.proactive_analysis_enabled,
        "min_interval_minutes": settings.proactive_analysis_min_interval_minutes,
        "max_runs_per_day": settings.proactive_analysis_max_runs_per_day,
        "max_daily_budget_usd": settings.proactive_analysis_max_daily_budget_usd,
        "owner_id": str(identity.get("subject", "")),
    }


@router.get("/plans")
def list_plans(identity: dict = Depends(require_query_access)):
    service = get_proactive_analysis_service()
    return [
        _plan_payload(item)
        for item in service.list(
            tenant_id=identity.get("tenant_id", "default"),
            owner_id=str(identity.get("subject", "")),
        )
    ]


@router.post("/plans")
def create_plan(request: ProactiveAnalysisCreateRequest, identity: dict = Depends(require_query_access)):
    datasource = get_datasource(request.datasource_id)
    if not datasource or not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="E_DATASOURCE_FORBIDDEN:数据源不存在或无权访问")
    service = get_proactive_analysis_service()
    return _call(
        lambda: service.create(
            tenant_id=identity.get("tenant_id", "default"),
            owner_id=str(identity.get("subject", "")),
            spec=ProactiveAnalysisSpec(**request.model_dump()),
        )
    )


def _transition(plan_id: str, action: str, identity: dict):
    if action in {"activate", "resume"} and not settings.proactive_analysis_enabled:
        raise HTTPException(status_code=409, detail="E_PROACTIVE_DISABLED:主动分析尚未显式启用")
    service = get_proactive_analysis_service()
    method = getattr(service, action)
    return _call(
        lambda: method(
            plan_id,
            tenant_id=identity.get("tenant_id", "default"),
            owner_id=str(identity.get("subject", "")),
        )
    )


@router.post("/plans/{plan_id}/activate")
def activate_plan(plan_id: str, identity: dict = Depends(require_query_access)):
    return _transition(plan_id, "activate", identity)


@router.post("/plans/{plan_id}/pause")
def pause_plan(plan_id: str, identity: dict = Depends(require_query_access)):
    return _transition(plan_id, "pause", identity)


@router.post("/plans/{plan_id}/resume")
def resume_plan(plan_id: str, identity: dict = Depends(require_query_access)):
    return _transition(plan_id, "resume", identity)


@router.post("/plans/{plan_id}/close")
def close_plan(plan_id: str, identity: dict = Depends(require_query_access)):
    return _transition(plan_id, "close", identity)


@router.get("/plans/{plan_id}/runs")
def list_runs(
    plan_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    identity: dict = Depends(require_query_access),
):
    try:
        values = get_proactive_analysis_service().list_runs(
            plan_id,
            tenant_id=identity.get("tenant_id", "default"),
            owner_id=str(identity.get("subject", "")),
            limit=limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_run_payload(item) for item in values]


__all__ = ["router"]
