"""MySQL persistence and atomic due-plan claiming."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.domain.proactive_analysis import (
    ProactiveAnalysisPlan,
    ProactiveAnalysisRun,
    ProactiveAnalysisSpec,
)
from app.mysql_db import connect


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class MySQLProactiveAnalysisRepository:
    _plan_columns = (
        "id,tenant_id,owner_id,question,datasource_id,interval_minutes,max_runs_per_day,"
        "daily_budget_usd,require_approval,state,next_run_at,last_run_at,last_task_id,"
        "runs_today,cost_today_usd,budget_date,lease_token,lease_expires_at,created_at,updated_at"
    )
    _run_columns = (
        "id,plan_id,tenant_id,owner_id,scheduled_for,status,task_id,trace_id,error_code,"
        "estimated_cost_usd,started_at,completed_at"
    )

    @classmethod
    def _plan(cls, row) -> ProactiveAnalysisPlan:
        return ProactiveAnalysisPlan(
            id=row[0],
            tenant_id=row[1],
            owner_id=row[2],
            spec=ProactiveAnalysisSpec(
                question=row[3],
                datasource_id=row[4],
                interval_minutes=int(row[5]),
                max_runs_per_day=int(row[6]),
                daily_budget_usd=float(row[7]),
                require_approval=bool(row[8]),
            ),
            state=row[9],
            next_run_at=_utc(row[10]),
            last_run_at=_utc(row[11]),
            last_task_id=row[12] or "",
            runs_today=int(row[13]),
            cost_today_usd=float(row[14]),
            budget_date=row[15],
            lease_token=row[16] or "",
            lease_expires_at=_utc(row[17]),
            created_at=_utc(row[18]),
            updated_at=_utc(row[19]),
        )

    @classmethod
    def _run(cls, row) -> ProactiveAnalysisRun:
        return ProactiveAnalysisRun(
            id=row[0],
            plan_id=row[1],
            tenant_id=row[2],
            owner_id=row[3],
            scheduled_for=_utc(row[4]),
            status=row[5],
            task_id=row[6] or "",
            trace_id=row[7] or "",
            error_code=row[8] or "",
            estimated_cost_usd=float(row[9]),
            started_at=_utc(row[10]),
            completed_at=_utc(row[11]),
        )

    def create(self, plan: ProactiveAnalysisPlan) -> ProactiveAnalysisPlan:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO proactive_analysis_plans "
                    "(id,tenant_id,owner_id,question,datasource_id,interval_minutes,max_runs_per_day,"
                    "daily_budget_usd,require_approval,state,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        plan.id,
                        plan.tenant_id,
                        plan.owner_id,
                        plan.spec.question,
                        plan.spec.datasource_id,
                        plan.spec.interval_minutes,
                        plan.spec.max_runs_per_day,
                        plan.spec.daily_budget_usd,
                        plan.spec.require_approval,
                        plan.state,
                        plan.created_at.replace(tzinfo=None),
                        plan.updated_at.replace(tzinfo=None),
                    ),
                )
            conn.commit()
        return plan

    def get(self, plan_id: str, tenant_id: str, owner_id: str) -> ProactiveAnalysisPlan | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._plan_columns} FROM proactive_analysis_plans "
                    "WHERE id=%s AND tenant_id=%s AND owner_id=%s",
                    (plan_id, tenant_id, owner_id),
                )
                row = cur.fetchone()
        return self._plan(row) if row else None

    def list(self, tenant_id: str, owner_id: str) -> list[ProactiveAnalysisPlan]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._plan_columns} FROM proactive_analysis_plans "
                    "WHERE tenant_id=%s AND owner_id=%s ORDER BY created_at DESC",
                    (tenant_id, owner_id),
                )
                rows = cur.fetchall()
        return [self._plan(row) for row in rows]

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
        placeholders = ",".join(["%s"] * len(expected_states))
        params = (
            state,
            next_run_at.replace(tzinfo=None) if next_run_at else None,
            plan_id,
            tenant_id,
            owner_id,
            *sorted(expected_states),
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE proactive_analysis_plans SET state=%s,next_run_at=%s,"
                    "lease_token=NULL,lease_expires_at=NULL "
                    f"WHERE id=%s AND tenant_id=%s AND owner_id=%s AND state IN ({placeholders})",
                    params,
                )
                changed = cur.rowcount
            conn.commit()
        if changed == 0:
            current = self.get(plan_id, tenant_id, owner_id)
            if current is None:
                return None
            raise ValueError(f"E_PROACTIVE_STATE:{current.state} 不能转换为 {state}")
        return self.get(plan_id, tenant_id, owner_id)

    def claim_due(self, *, now: datetime, limit: int, lease_seconds: int) -> list[ProactiveAnalysisPlan]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,tenant_id,owner_id FROM proactive_analysis_plans "
                    "WHERE state='active' AND next_run_at<=%s "
                    "AND (lease_expires_at IS NULL OR lease_expires_at<=%s) "
                    "ORDER BY next_run_at,id LIMIT %s",
                    (now.replace(tzinfo=None), now.replace(tzinfo=None), max(1, min(limit, 100))),
                )
                candidates = cur.fetchall()
            conn.commit()
        claimed = []
        for plan_id, tenant_id, owner_id in candidates:
            token = str(uuid4())
            expires = now + timedelta(seconds=lease_seconds)
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE proactive_analysis_plans SET lease_token=%s,lease_expires_at=%s "
                        "WHERE id=%s AND tenant_id=%s AND owner_id=%s AND state='active' "
                        "AND next_run_at<=%s AND (lease_expires_at IS NULL OR lease_expires_at<=%s)",
                        (
                            token,
                            expires.replace(tzinfo=None),
                            plan_id,
                            tenant_id,
                            owner_id,
                            now.replace(tzinfo=None),
                            now.replace(tzinfo=None),
                        ),
                    )
                    changed = cur.rowcount
                conn.commit()
            if changed:
                item = self.get(plan_id, tenant_id, owner_id)
                if item:
                    claimed.append(item)
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
        run_date = run_at.date()
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE proactive_analysis_plans SET "
                    "state=COALESCE(%s,state),next_run_at=%s,"
                    "last_run_at=CASE WHEN %s THEN %s ELSE last_run_at END,"
                    "last_task_id=CASE WHEN %s<>'' THEN %s ELSE last_task_id END,"
                    "runs_today=CASE WHEN budget_date=%s THEN runs_today ELSE 0 END + %s,"
                    "cost_today_usd=CASE WHEN budget_date=%s THEN cost_today_usd ELSE 0 END + %s,"
                    "budget_date=%s,lease_token=NULL,lease_expires_at=NULL "
                    "WHERE id=%s AND tenant_id=%s AND lease_token=%s",
                    (
                        state,
                        next_run_at.replace(tzinfo=None) if next_run_at else None,
                        count_run,
                        run_at.replace(tzinfo=None),
                        task_id,
                        task_id,
                        run_date,
                        1 if count_run else 0,
                        run_date,
                        max(0.0, float(cost_usd)) if count_run else 0.0,
                        run_date,
                        plan_id,
                        tenant_id,
                        lease_token,
                    ),
                )
                changed = cur.rowcount
            conn.commit()
        if not changed:
            return None
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._plan_columns} FROM proactive_analysis_plans WHERE id=%s AND tenant_id=%s",
                    (plan_id, tenant_id),
                )
                row = cur.fetchone()
        return self._plan(row) if row else None

    def create_run(self, run: ProactiveAnalysisRun) -> ProactiveAnalysisRun:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO proactive_analysis_runs "
                    "(id,plan_id,tenant_id,owner_id,scheduled_for,status,task_id,trace_id,error_code,"
                    "estimated_cost_usd,started_at,completed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        run.id,
                        run.plan_id,
                        run.tenant_id,
                        run.owner_id,
                        run.scheduled_for.replace(tzinfo=None),
                        run.status,
                        run.task_id or None,
                        run.trace_id or None,
                        run.error_code or None,
                        run.estimated_cost_usd,
                        run.started_at.replace(tzinfo=None),
                        run.completed_at.replace(tzinfo=None),
                    ),
                )
            conn.commit()
        return run

    def list_runs(self, plan_id: str, *, tenant_id: str, owner_id: str, limit: int = 50) -> list[ProactiveAnalysisRun]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._run_columns} FROM proactive_analysis_runs "
                    "WHERE plan_id=%s AND tenant_id=%s AND owner_id=%s "
                    "ORDER BY started_at DESC LIMIT %s",
                    (plan_id, tenant_id, owner_id, max(1, min(limit, 100))),
                )
                rows = cur.fetchall()
        return [self._run(row) for row in rows]


__all__ = ["MySQLProactiveAnalysisRepository"]
