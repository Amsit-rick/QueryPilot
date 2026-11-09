"""HTTP contracts for proactive analysis governance."""
from pydantic import BaseModel, Field


class ProactiveAnalysisCreateRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    datasource_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    interval_minutes: int = Field(default=60, ge=1, le=10080)
    max_runs_per_day: int = Field(default=6, ge=1, le=100)
    daily_budget_usd: float = Field(default=0.5, ge=0, le=100)
    require_approval: bool = True


__all__ = ["ProactiveAnalysisCreateRequest"]
