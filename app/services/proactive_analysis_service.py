"""Lifecycle governance for user-authorized proactive analysis plans."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.domain.proactive_analysis import ProactiveAnalysisPlan, ProactiveAnalysisSpec
from app.repositories.memory_proactive_analysis_repository import InMemoryProactiveAnalysisRepository


class ProactiveAnalysisService:
    def __init__(
        self,
        *,
        repository=None,
        min_interval_minutes: int = 15,
        max_runs_per_day: int = 24,
        max_daily_budget_usd: float = 2.0,
        now: Callable[[], datetime] | None = None,
        audit_writer=None,
    ):
        self.repository = repository or InMemoryProactiveAnalysisRepository()
        self.min_interval_minutes = int(min_interval_minutes)
        self.max_runs_per_day = int(max_runs_per_day)
        self.max_daily_budget_usd = float(max_daily_budget_usd)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.audit_writer = audit_writer

    def _audit(self, event: str, plan: ProactiveAnalysisPlan, actor_id: str) -> None:
        if self.audit_writer:
            self.audit_writer(
                event=event,
                plan_id=plan.id,
                tenant_id=plan.tenant_id,
                actor_id=actor_id,
                state=plan.state,
            )

    def _validate_spec(self, spec: ProactiveAnalysisSpec) -> None:
        if spec.interval_minutes < self.min_interval_minutes:
            raise ValueError(
                f"E_PROACTIVE_INTERVAL:执行间隔不能小于 {self.min_interval_minutes} 分钟"
            )
        if spec.max_runs_per_day > self.max_runs_per_day:
            raise ValueError(
                f"E_PROACTIVE_RUN_LIMIT:单日执行次数不能超过 {self.max_runs_per_day}"
            )
        if spec.daily_budget_usd > self.max_daily_budget_usd:
            raise ValueError(
                f"E_PROACTIVE_BUDGET:单计划日预算不能超过 {self.max_daily_budget_usd} 美元"
            )

    def create(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        spec: ProactiveAnalysisSpec,
    ) -> ProactiveAnalysisPlan:
        self._validate_spec(spec)
        created = self.repository.create(
            ProactiveAnalysisPlan(
                tenant_id=tenant_id,
                owner_id=owner_id,
                spec=spec,
            )
        )
        self._audit("proactive_plan_created", created, owner_id)
        return created

    def get(self, plan_id: str, *, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan:
        item = self.repository.get(plan_id, tenant_id, owner_id)
        if item is None:
            raise LookupError("主动分析计划不存在")
        return item

    def list(self, *, tenant_id: str, owner_id: str) -> list[ProactiveAnalysisPlan]:
        return self.repository.list(tenant_id, owner_id)

    def list_runs(self, plan_id: str, *, tenant_id: str, owner_id: str, limit: int = 50):
        self.get(plan_id, tenant_id=tenant_id, owner_id=owner_id)
        return self.repository.list_runs(
            plan_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            limit=max(1, min(int(limit), 100)),
        )

    def _transition(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        expected_states: set[str],
        state: str,
        next_run_at: datetime | None,
        event: str,
    ) -> ProactiveAnalysisPlan:
        current = self.get(plan_id, tenant_id=tenant_id, owner_id=owner_id)
        if current.state not in expected_states:
            raise ValueError(f"E_PROACTIVE_STATE:{current.state} 不能转换为 {state}")
        updated = self.repository.transition(
            plan_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            expected_states=expected_states,
            state=state,
            next_run_at=next_run_at,
        )
        if updated is None:
            raise LookupError("主动分析计划不存在")
        self._audit(event, updated, owner_id)
        return updated

    def activate(self, plan_id: str, *, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan:
        return self._transition(
            plan_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            expected_states={"draft"},
            state="active",
            next_run_at=self.now(),
            event="proactive_plan_activated",
        )

    def pause(self, plan_id: str, *, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan:
        return self._transition(
            plan_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            expected_states={"active"},
            state="paused",
            next_run_at=None,
            event="proactive_plan_paused",
        )

    def resume(self, plan_id: str, *, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan:
        return self._transition(
            plan_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            expected_states={"paused"},
            state="active",
            next_run_at=self.now(),
            event="proactive_plan_resumed",
        )

    def close(self, plan_id: str, *, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan:
        return self._transition(
            plan_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            expected_states={"draft", "active", "paused"},
            state="closed",
            next_run_at=None,
            event="proactive_plan_closed",
        )


__all__ = ["ProactiveAnalysisService"]
