from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReadOnlyToolCallRequest(BaseModel):
    """Stable envelope shared by HTTP and embedded read-only integrations."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, max_length=128)
