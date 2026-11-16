"""HTTP contracts for the Agent skill catalog and router."""

from typing import Literal

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


class SkillImportSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["local", "github", "registry"]
    uri: str = Field(min_length=1, max_length=2048)
    revision: str = Field(min_length=1, max_length=128)
    publisher: str = Field(min_length=1, max_length=128)


class SkillImportSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["sha256"]
    digest: str = Field(pattern=r"^[0-9A-Fa-f]{64}$")
    issuer: str = Field(min_length=1, max_length=128)


class SkillImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=131072)
    source: SkillImportSource
    signature: SkillImportSignature


class SkillImportConfirmRequest(SkillImportPreviewRequest):
    confirmation_token: str = Field(min_length=32, max_length=4096)


__all__ = [
    "SkillCanaryRequest",
    "SkillEvaluationRequest",
    "SkillImportConfirmRequest",
    "SkillImportPreviewRequest",
    "SkillImportSignature",
    "SkillImportSource",
    "SkillPreviewRequest",
    "SkillRouteRequest",
    "SkillVersionCreateRequest",
]
