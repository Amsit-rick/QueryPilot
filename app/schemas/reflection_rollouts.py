"""HTTP contracts for tenant-scoped reflection rollout governance."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.reflection_rollout import ReflectionRolloutPolicy


class ReflectionRolloutCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: ReflectionRolloutPolicy


class ReflectionRolloutCanaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    percentage: int = Field(ge=1, le=100)


class ReflectionRolloutPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=128)


class ReflectionRolloutHealthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_acceptance_rate: float = Field(default=0, ge=0, le=1)
    invalid_retry_rate: float = Field(default=0, ge=0, le=1)
    p95_latency_ms: float = Field(default=0, ge=0, le=300000)
    security_permission_bypasses: int = Field(default=0, ge=0)


__all__ = [
    "ReflectionRolloutCanaryRequest",
    "ReflectionRolloutCreateRequest",
    "ReflectionRolloutHealthRequest",
    "ReflectionRolloutPreviewRequest",
]

