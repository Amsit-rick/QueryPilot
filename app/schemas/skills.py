"""HTTP contracts for the Agent skill catalog and router."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.skill import SkillDefinition


class SkillRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    datasource_id: str | None = Field(default=None, min_length=1, max_length=128)


class SkillVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: SkillDefinition


class SkillEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: SkillDefinition


class SkillCanaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    percentage: int = Field(ge=1, le=100)


class SkillPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]+$")
    subject: str = Field(min_length=1, max_length=128)


__all__ = [
    "SkillCanaryRequest",
    "SkillEvaluationRequest",
    "SkillPreviewRequest",
    "SkillRouteRequest",
    "SkillVersionCreateRequest",
]
