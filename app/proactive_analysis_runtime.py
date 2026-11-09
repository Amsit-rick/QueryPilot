"""Runtime wiring for proactive analysis plans and one-shot execution."""
from __future__ import annotations

from app.audit import write_audit
from app.budget import estimate_query_cost
from app.config import settings
from app.identity_store import get_user_by_id
from app.repositories.memory_proactive_analysis_repository import InMemoryProactiveAnalysisRepository
from app.repositories.mysql_proactive_analysis_repository import MySQLProactiveAnalysisRepository
from app.services.proactive_analysis_runner import ProactiveAnalysisRunner
from app.services.proactive_analysis_service import ProactiveAnalysisService


_memory_repository = InMemoryProactiveAnalysisRepository()
_mysql_repository = MySQLProactiveAnalysisRepository()


def _audit(**payload) -> None:
    write_audit(
        settings.audit_log_path,
        trace_id=str(payload.get("run_id") or payload.get("plan_id") or "proactive-analysis"),
        task_id=str(payload.get("task_id") or "") or None,
        tenant_id=str(payload.get("tenant_id") or "default"),
        actor_id=str(payload.get("actor_id") or ""),
        sql="",
        duration_ms=0,
        event=str(payload.get("event") or "proactive_analysis"),
        error_code=str(payload.get("error_code") or "") or None,
        operation_name="proactive_analysis",
    )


def get_proactive_analysis_service() -> ProactiveAnalysisService:
    repository = _mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository
    return ProactiveAnalysisService(
        repository=repository,
        min_interval_minutes=settings.proactive_analysis_min_interval_minutes,
        max_runs_per_day=settings.proactive_analysis_max_runs_per_day,
        max_daily_budget_usd=settings.proactive_analysis_max_daily_budget_usd,
        audit_writer=_audit,
    )


def _resolve_identity(plan) -> dict:
    if not settings.auth_enabled and plan.owner_id == "local-dev":
        return {"subject": "local-dev", "tenant_id": plan.tenant_id, "roles": ["analyst"]}
    if not str(plan.owner_id).isdigit():
        raise RuntimeError("E_PROACTIVE_IDENTITY_UNSUPPORTED")
    user = get_user_by_id(int(plan.owner_id))
    if not user or not user.get("is_active"):
        raise RuntimeError("E_PROACTIVE_IDENTITY_REVOKED")
    if str(user.get("tenant_id")) != plan.tenant_id:
        raise RuntimeError("E_PROACTIVE_IDENTITY_TENANT")
    return {
        "subject": str(user["id"]),
        "tenant_id": str(user["tenant_id"]),
        "roles": [str(user["role"])],
    }


def get_proactive_analysis_runner() -> ProactiveAnalysisRunner:
    if not settings.proactive_analysis_enabled:
        raise RuntimeError("E_PROACTIVE_DISABLED:请先显式启用 PROACTIVE_ANALYSIS_ENABLED")

    async def execute(request, identity):
        from app.main import get_query_application_service

        return await get_query_application_service().execute(request, identity)

    return ProactiveAnalysisRunner(
        service=get_proactive_analysis_service(),
        query_executor=execute,
        identity_resolver=_resolve_identity,
        estimate_cost=lambda question: estimate_query_cost(question, None),
        lease_seconds=settings.proactive_analysis_lease_seconds,
        audit_writer=_audit,
    )


def reset_memory_proactive_analysis_repository() -> None:
    _memory_repository.clear()


__all__ = [
    "get_proactive_analysis_runner",
    "get_proactive_analysis_service",
    "reset_memory_proactive_analysis_repository",
]
