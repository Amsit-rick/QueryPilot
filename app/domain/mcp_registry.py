"""Domain contract for governed external MCP server registrations."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class McpServerRegistration:
    tenant_id: str
    name: str
    transport: str
    endpoint: str
    auth_env_ref: str
    allowed_tools: tuple[str, ...]
    enabled: bool = True
    read_only_only: bool = True
    allow_private_network: bool = False
    request_timeout_seconds: float = 10.0
    max_concurrency: int = 4
    daily_call_limit: int = 1000
    circuit_failure_threshold: int = 3
    circuit_reset_seconds: int = 30
    health_status: str = "unchecked"
    last_latency_ms: int = 0
    last_error_code: str = ""
    discovered_tools: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    last_checked_at: datetime | None = None

