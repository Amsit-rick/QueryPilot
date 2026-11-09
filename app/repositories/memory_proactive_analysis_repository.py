"""Thread-safe in-memory proactive analysis repository."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from app.domain.proactive_analysis import ProactiveAnalysisPlan, ProactiveAnalysisRun


class InMemoryProactiveAnalysisRepository:
    def __init__(self):
        self._plans: dict[str, ProactiveAnalysisPlan] = {}
        self._runs: dict[str, ProactiveAnalysisRun] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._plans.clear()
            self._runs.clear()

    def create(self, plan: ProactiveAnalysisPlan) -> ProactiveAnalysisPlan:
        with self._lock:
            self._plans[plan.id] = plan
            return replace(plan)

    def get(self, plan_id: str, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan | None:
        with self._lock:
            item = self._plans.get(plan_id)
            if not item or item.tenant_id != tenant_id or item.owner_id != owner_id:
                return None
            return replace(item)

    def list(self, tenant_id: str, owner_id: str) -> list[ProactiveAnalysisPlan]:
        with self._lock:
            items = [
                replace(item)
                for item in self._plans.values()
                if item.tenant_id == tenant_id and item.owner_id == owner_id
            ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def transition(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        expected_states: set[str],
        state: str,
        next_run_at: datetime | None,
    ) -> ProactiveAnalysisPlan | None:
        with self._lock:
            current = self._plans.get(plan_id)
            if not current or current.tenant_id != tenant_id or current.owner_id != owner_id:
                return None
            if current.state not in expected_states:
                raise ValueError(f"E_PROACTIVE_STATE:{current.state} 不能转换为 {state}")
            updated = replace(
                current,
                state=state,
                next_run_at=next_run_at,
                lease_token="",
                lease_expires_at=None,
                updated_at=datetime.now(timezone.utc),
            )
            self._plans[plan_id] = updated
            return replace(updated)

    def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> list[ProactiveAnalysisPlan]:
        claimed: list[ProactiveAnalysisPlan] = []
        with self._lock:
            due = sorted(
                (
                    item
                    for item in self._plans.values()
                    if item.state == "active"
                    and item.next_run_at is not None
                    and item.next_run_at <= now
                    and (item.lease_expires_at is None or item.lease_expires_at <= now)
                ),
                key=lambda item: (item.next_run_at, item.id),
            )[: max(0, limit)]
            for item in due:
                updated = replace(
                    item,
                    lease_token=str(uuid4()),
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                self._plans[item.id] = updated
                claimed.append(replace(updated))
        return claimed

    def complete_claim(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        lease_token: str,
        run_at: datetime,
        next_run_at: datetime | None,
        task_id: str,
        cost_usd: float,
        count_run: bool,
        state: str | None = None,
    ) -> ProactiveAnalysisPlan | None:
        with self._lock:
            current = self._plans.get(plan_id)
            if not current or current.tenant_id != tenant_id or current.lease_token != lease_token:
                return None
            same_day = current.budget_date == run_at.date()
            runs_today = current.runs_today if same_day else 0
            cost_today = current.cost_today_usd if same_day else 0.0
            if count_run:
                runs_today += 1
                cost_today += max(0.0, float(cost_usd))
            updated = replace(
                current,
                state=state or current.state,
                next_run_at=next_run_at,
                last_run_at=run_at if count_run else current.last_run_at,
                last_task_id=task_id or current.last_task_id,
                runs_today=runs_today,
                cost_today_usd=round(cost_today, 8),
                budget_date=run_at.date(),
                lease_token="",
                lease_expires_at=None,
                updated_at=run_at,
            )
            self._plans[plan_id] = updated
            return replace(updated)

    def create_run(self, run: ProactiveAnalysisRun) -> ProactiveAnalysisRun:
        with self._lock:
            self._runs[run.id] = run
            return replace(run)

    def list_runs(
        self,
        plan_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        limit: int = 50,
    ) -> list[ProactiveAnalysisRun]:
        with self._lock:
            values = [
                replace(item)
                for item in self._runs.values()
                if item.plan_id == plan_id
                and item.tenant_id == tenant_id
                and item.owner_id == owner_id
            ]
        return sorted(values, key=lambda item: item.started_at, reverse=True)[:limit]


__all__ = ["InMemoryProactiveAnalysisRepository"]
