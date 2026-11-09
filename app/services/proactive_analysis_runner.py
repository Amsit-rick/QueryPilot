"""One-shot runner for due proactive analysis plans."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable

from fastapi import HTTPException

from app.domain.proactive_analysis import ProactiveAnalysisPlan, ProactiveAnalysisRun
from app.schemas.queries import QueryRequest
from app.services.proactive_analysis_service import ProactiveAnalysisService


@dataclass(frozen=True)
class ProactiveAnalysisDecision:
    plan_id: str
    run_id: str
    action: str
    task_id: str = ""
    error_code: str = ""


@dataclass(frozen=True)
class ProactiveAnalysisReport:
    scanned: int
    completed: int
    approval_required: int
    skipped_budget: int
    failed: int
    decisions: tuple[ProactiveAnalysisDecision, ...]


class ProactiveAnalysisRunner:
    def __init__(
        self,
        *,
        service: ProactiveAnalysisService,
        query_executor: Callable[[QueryRequest, dict], Awaitable[dict]],
        identity_resolver: Callable[[ProactiveAnalysisPlan], dict],
        estimate_cost: Callable[[str], float],
        now: Callable[[], datetime] | None = None,
        lease_seconds: int = 120,
        audit_writer=None,
    ):
        self.service = service
        self.query_executor = query_executor
        self.identity_resolver = identity_resolver
        self.estimate_cost = estimate_cost
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = max(5, int(lease_seconds))
        self.audit_writer = audit_writer

    @staticmethod
    def _code(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                return str(detail.get("code") or "E_PROACTIVE_QUERY")
            candidate = str(detail).split(":", 1)[0]
            return candidate if candidate.startswith("E_") else "E_PROACTIVE_QUERY"
        candidate = str(exc).split(":", 1)[0]
        return candidate if candidate.startswith("E_") else "E_PROACTIVE_RUNNER"

    @staticmethod
    def _next_interval(plan: ProactiveAnalysisPlan, now: datetime) -> datetime:
        anchor = max(now, plan.next_run_at or now)
        return anchor + timedelta(minutes=plan.spec.interval_minutes)

    @staticmethod
    def _next_utc_day(now: datetime) -> datetime:
        next_day = now.date() + timedelta(days=1)
        return datetime.combine(next_day, time.min, tzinfo=timezone.utc)

    def _audit(self, *, event: str, plan: ProactiveAnalysisPlan, run: ProactiveAnalysisRun) -> None:
        if self.audit_writer:
            self.audit_writer(
                event=event,
                plan_id=plan.id,
                run_id=run.id,
                tenant_id=plan.tenant_id,
                actor_id=plan.owner_id,
                task_id=run.task_id,
                status=run.status,
                error_code=run.error_code,
            )

    def _record(
        self,
        plan: ProactiveAnalysisPlan,
        *,
        status: str,
        now: datetime,
        estimated_cost: float,
        task_id: str = "",
        trace_id: str = "",
        error_code: str = "",
    ) -> ProactiveAnalysisRun:
        run = self.service.repository.create_run(
            ProactiveAnalysisRun(
                plan_id=plan.id,
                tenant_id=plan.tenant_id,
                owner_id=plan.owner_id,
                scheduled_for=plan.next_run_at or now,
                status=status,
                task_id=task_id,
                trace_id=trace_id,
                error_code=error_code,
                estimated_cost_usd=max(0.0, estimated_cost),
                started_at=now,
                completed_at=now,
            )
        )
        self._audit(event="proactive_run_recorded", plan=plan, run=run)
        return run

    async def run_once(self, *, limit: int = 20) -> ProactiveAnalysisReport:
        now = self.now()
        plans = self.service.repository.claim_due(
            now=now,
            limit=max(1, min(int(limit), 100)),
            lease_seconds=self.lease_seconds,
        )
        decisions: list[ProactiveAnalysisDecision] = []
        counters = {"completed": 0, "approval_required": 0, "skipped_budget": 0, "failed": 0}

        for plan in plans:
            try:
                estimated_cost = max(0.0, float(self.estimate_cost(plan.spec.question)))
            except Exception:
                completed_claim = self.service.repository.complete_claim(
                    plan.id,
                    tenant_id=plan.tenant_id,
                    lease_token=plan.lease_token,
                    run_at=now,
                    next_run_at=self._next_interval(plan, now),
                    task_id="",
                    cost_usd=0,
                    count_run=False,
                )
                error_code = (
                    "E_PROACTIVE_COST_ESTIMATE"
                    if completed_claim is not None
                    else "E_PROACTIVE_LEASE_STALE"
                )
                run = self._record(
                    plan,
                    status="failed",
                    now=now,
                    estimated_cost=0,
                    error_code=error_code,
                )
                counters["failed"] += 1
                decisions.append(
                    ProactiveAnalysisDecision(
                        plan.id,
                        run.id,
                        "failed",
                        error_code=error_code,
                    )
                )
                continue
            same_day = plan.budget_date == now.date()
            runs_today = plan.runs_today if same_day else 0
            cost_today = plan.cost_today_usd if same_day else 0.0
            limit_code = ""
            if runs_today >= plan.spec.max_runs_per_day:
                limit_code = "E_PROACTIVE_DAILY_RUN_LIMIT"
            elif cost_today + estimated_cost > plan.spec.daily_budget_usd:
                limit_code = "E_PROACTIVE_DAILY_BUDGET"
            if limit_code:
                completed_claim = self.service.repository.complete_claim(
                    plan.id,
                    tenant_id=plan.tenant_id,
                    lease_token=plan.lease_token,
                    run_at=now,
                    next_run_at=max(self._next_interval(plan, now), self._next_utc_day(now)),
                    task_id="",
                    cost_usd=0,
                    count_run=False,
                )
                status = "skipped_budget" if completed_claim is not None else "failed"
                error_code = limit_code if completed_claim is not None else "E_PROACTIVE_LEASE_STALE"
                run = self._record(
                    plan,
                    status=status,
                    now=now,
                    estimated_cost=estimated_cost,
                    error_code=error_code,
                )
                counters[status] += 1
                decisions.append(
                    ProactiveAnalysisDecision(
                        plan.id,
                        run.id,
                        status,
                        error_code=error_code,
                    )
                )
                continue

            try:
                identity = self.identity_resolver(plan)
            except Exception as exc:
                code = self._code(exc)
                completed_claim = self.service.repository.complete_claim(
                    plan.id,
                    tenant_id=plan.tenant_id,
                    lease_token=plan.lease_token,
                    run_at=now,
                    next_run_at=None,
                    task_id="",
                    cost_usd=0,
                    count_run=False,
                    state="paused",
                )
                if completed_claim is None:
                    code = "E_PROACTIVE_LEASE_STALE"
                run = self._record(
                    plan,
                    status="failed",
                    now=now,
                    estimated_cost=estimated_cost,
                    error_code=code,
                )
                counters["failed"] += 1
                decisions.append(ProactiveAnalysisDecision(plan.id, run.id, "failed", error_code=code))
                continue

            request = QueryRequest(
                question=plan.spec.question,
                datasource=plan.spec.datasource_id,
                require_approval=plan.spec.require_approval,
                client_request_id=f"proactive:{plan.id}:{int((plan.next_run_at or now).timestamp())}",
            )
            try:
                result = await self.query_executor(request, identity)
                error = result.get("error") if isinstance(result, dict) else None
                if isinstance(error, dict):
                    status = "failed"
                    error_code = str(error.get("code") or "E_PROACTIVE_QUERY")
                elif result.get("status") == "approval_required":
                    status = "approval_required"
                    error_code = ""
                else:
                    status = "completed"
                    error_code = ""
                task_id = str(result.get("task_id", ""))
                trace_id = str(result.get("trace_id", ""))
            except Exception as exc:
                status = "failed"
                error_code = self._code(exc)
                task_id = ""
                trace_id = ""

            pause = error_code.startswith(("E_PROACTIVE_IDENTITY", "E_DATASOURCE_FORBIDDEN"))
            completed_claim = self.service.repository.complete_claim(
                plan.id,
                tenant_id=plan.tenant_id,
                lease_token=plan.lease_token,
                run_at=now,
                next_run_at=None if pause else self._next_interval(plan, now),
                task_id=task_id,
                cost_usd=estimated_cost,
                count_run=True,
                state="paused" if pause else None,
            )
            if completed_claim is None:
                status = "failed"
                error_code = "E_PROACTIVE_LEASE_STALE"
            run = self._record(
                plan,
                status=status,
                now=now,
                estimated_cost=estimated_cost,
                task_id=task_id,
                trace_id=trace_id,
                error_code=error_code,
            )
            counters[status] += 1
            decisions.append(
                ProactiveAnalysisDecision(
                    plan_id=plan.id,
                    run_id=run.id,
                    action=status,
                    task_id=task_id,
                    error_code=error_code,
                )
            )

        return ProactiveAnalysisReport(
            scanned=len(plans),
            completed=counters["completed"],
            approval_required=counters["approval_required"],
            skipped_budget=counters["skipped_budget"],
            failed=counters["failed"],
            decisions=tuple(decisions),
        )


__all__ = [
    "ProactiveAnalysisDecision",
    "ProactiveAnalysisReport",
    "ProactiveAnalysisRunner",
]
