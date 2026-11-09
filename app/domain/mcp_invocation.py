"""Runtime contracts for external MCP discovery and invocation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class McpToolDescriptor:
    name: str
    read_only: bool | None
    destructive: bool | None
    idempotent: bool | None

