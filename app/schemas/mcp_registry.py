"""Strict HTTP request contracts for the external MCP registry."""

from pydantic import BaseModel, ConfigDict, Field


class McpServerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    transport: str = "streamable-http"
    endpoint: str = Field(min_length=1, max_length=500)
    auth_env_ref: str = Field(min_length=2, max_length=128)
    allowed_tools: list[str] = Field(min_length=1, max_length=100)
    enabled: bool = True
    read_only_only: bool = True
    allow_private_network: bool = False
    request_timeout_seconds: float = Field(default=10.0, ge=1, le=120)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    daily_call_limit: int = Field(default=1000, ge=1, le=1_000_000)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    circuit_reset_seconds: int = Field(default=30, ge=1, le=3600)


class McpServerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    transport: str | None = None
    endpoint: str | None = Field(default=None, min_length=1, max_length=500)
    auth_env_ref: str | None = Field(default=None, min_length=2, max_length=128)
    allowed_tools: list[str] | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    read_only_only: bool | None = None
    allow_private_network: bool | None = None
    request_timeout_seconds: float | None = Field(default=None, ge=1, le=120)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    daily_call_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    circuit_failure_threshold: int | None = Field(default=None, ge=1, le=20)
    circuit_reset_seconds: int | None = Field(default=None, ge=1, le=3600)


class McpToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)

