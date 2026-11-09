"""Versioned, declarative Agent skill contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SKILL_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class SkillDefinition(BaseModel):
    """A safe workflow declaration, not executable model-provided code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    intents: tuple[str, ...] = Field(min_length=1, max_length=12)
    trigger_terms: tuple[str, ...] = Field(min_length=1, max_length=30)
    allowed_tools: tuple[
        Literal["schema.list", "query.plan", "task.status", "result.get"], ...
    ] = Field(min_length=1, max_length=8)
    workflow_steps: tuple[str, ...] = Field(min_length=1, max_length=12)
    allowed_roles: tuple[Literal["viewer", "analyst", "admin"], ...] = (
        "viewer",
        "analyst",
        "admin",
    )
    read_only: Literal[True] = True
    priority: int = Field(default=0, ge=0, le=100)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _SKILL_ID.fullmatch(value):
            raise ValueError("skill id must be lowercase kebab-case")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("skill version must be semantic version x.y.z")
        return value

    @field_validator("intents", "trigger_terms", "allowed_tools", "workflow_steps", "allowed_roles")
    @classmethod
    def validate_unique_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in values if item.strip())
        if len(cleaned) != len(values) or len(set(cleaned)) != len(cleaned):
            raise ValueError("skill list fields must contain unique non-empty values")
        return cleaned

    @field_validator("intents")
    @classmethod
    def validate_intent_lengths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value) > 80 for value in values):
            raise ValueError("skill intents must not exceed 80 characters")
        return values

    @field_validator("trigger_terms")
    @classmethod
    def validate_trigger_lengths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value) > 80 for value in values):
            raise ValueError("skill trigger terms must not exceed 80 characters")
        return values

    @field_validator("workflow_steps")
    @classmethod
    def validate_step_lengths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value) > 180 for value in values):
            raise ValueError("skill workflow steps must not exceed 180 characters")
        return values

    def public_dict(self) -> dict:
        """Expose workflow metadata without hidden prompts or executable code."""
        return self.model_dump(exclude={"trigger_terms", "priority"}, mode="json")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SkillVersion:
    """Persisted release metadata for one declarative Skill version."""

    tenant_id: str
    definition: SkillDefinition
    state: Literal["draft", "canary", "active", "retired"] = "draft"
    canary_percent: int = 0
    created_by: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


__all__ = ["SkillDefinition", "SkillVersion"]
