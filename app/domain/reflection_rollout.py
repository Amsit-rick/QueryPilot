"""Tenant-scoped release contracts for result-reflection rollout."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class ReflectionRolloutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    mode: Literal["off", "shadow", "enforced"] = "shadow"
    max_error_acceptance_rate: float = Field(default=0.01, ge=0, le=1)
    max_invalid_retry_rate: float = Field(default=0.05, ge=0, le=1)
    max_p95_latency_ms: int = Field(default=5000, ge=1, le=300000)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("reflection rollout version must be semantic version x.y.z")
        return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReflectionRolloutVersion:
    tenant_id: str
    policy: ReflectionRolloutPolicy
    state: Literal["draft", "canary", "active", "retired"] = "draft"
    canary_percent: int = 0
    created_by: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class ReflectionRolloutDecision:
    tenant_id: str
    subject: str
    requested_mode: str
    effective_mode: str
    policy_version: str
    deployment_state: str
    bucket: int
    gate_passed: bool
    reason: str


__all__ = [
    "ReflectionRolloutDecision",
    "ReflectionRolloutPolicy",
    "ReflectionRolloutVersion",
]

