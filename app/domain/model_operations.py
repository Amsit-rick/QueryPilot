from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ModelProvider:
    tenant_id: str
    name: str
    adapter: str
    base_url: str
    model: str
    api_key_env: str
    enabled: bool = True
    is_default: bool = False
    health_status: str = "unchecked"
    last_latency_ms: int = 0
    last_error_code: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    last_checked_at: datetime | None = None


@dataclass
class PromptTemplate:
    tenant_id: str
    name: str
    system_prompt: str
    version: int
    owner_id: str
    active: bool = False
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class RuntimeModelConfig:
    provider_id: str
    provider_name: str
    adapter: str
    base_url: str
    model: str
    api_key: str
    prompt_id: str
    prompt_version: str
    system_prompt: str
    source: str
