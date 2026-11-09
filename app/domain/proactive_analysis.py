"""Contracts for explicitly authorized proactive analysis schedules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProactiveAnalysisSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=2000)
    datasource_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    interval_minutes: int = Field(default=60, ge=1, le=10080)
    max_runs_per_day: int = Field(default=6, ge=1, le=100)
    daily_budget_usd: float = Field(default=0.5, ge=0, le=100)
    require_approval: bool = True


@dataclass(frozen=True)
class ProactiveAnalysisPlan:
    tenant_id: str
    owner_id: str
    spec: ProactiveAnalysisSpec
    state: Literal["draft", "active", "paused", "closed"] = "draft"
    id: str = field(default_factory=lambda: str(uuid4()))
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_task_id: str = ""
    runs_today: int = 0
    cost_today_usd: float = 0.0
    budget_date: date | None = None
    lease_token: str = ""
    lease_expires_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ProactiveAnalysisRun:
    plan_id: str
    tenant_id: str
    owner_id: str
    scheduled_for: datetime
    status: Literal[
        "completed",
        "approval_required",
        "failed",
        "skipped_budget",
    ]
    id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    trace_id: str = ""
    error_code: str = ""
    estimated_cost_usd: float = 0.0
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime = field(default_factory=utc_now)


__all__ = [
    "ProactiveAnalysisPlan",
    "ProactiveAnalysisRun",
    "ProactiveAnalysisSpec",
    "utc_now",
]
