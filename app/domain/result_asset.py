"""Uniform contract for query result assets.

The existing ``TaskArtifact`` in ``app.domain.task`` only carries ``task_id``,
``kind``, ``name``, ``uri`` and free-form ``metadata``. It has no tenant, no
owner, no provenance and no expiry, which makes per-asset re-authorization and
retention impossible.

``ResultAsset`` is the replacement contract. It is intentionally standalone so
it can be introduced without editing the current task domain module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

TABLE = "table"
SQL = "sql"
CSV = "csv"
CHART = "chart"
REPORT = "report"

RESULT_ASSET_KINDS: frozenset[str] = frozenset({TABLE, SQL, CSV, CHART, REPORT})

# Roles allowed to reach assets owned by another user inside the same tenant.
CROSS_USER_ROLES: frozenset[str] = frozenset({"admin"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AccessContext:
    """Who is asking for an asset. Never carries credentials or raw data."""

    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()

    def has_role(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)


@dataclass
class ResultAsset:
    """A durable, tenant-scoped result produced by a task step."""

    task_id: str
    tenant_id: str
    owner_id: str
    kind: str
    name: str
    uri: str = ""
    source_step_id: str = ""
    semantic_version: str = ""
    data_version: str = ""
    checksum: str = ""
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.kind not in RESULT_ASSET_KINDS:
            raise ValueError(f"E_ASSET_KIND:不支持的结果资产类型 {self.kind!r}")
        if not self.name.strip():
            raise ValueError("E_ASSET_NAME:结果资产名称不能为空")
        if not self.tenant_id:
            raise ValueError("E_ASSET_TENANT:结果资产必须绑定租户")
        if not self.owner_id:
            raise ValueError("E_ASSET_OWNER:结果资产必须绑定所属用户")
        if self.size_bytes < 0:
            raise ValueError("E_ASSET_SIZE:结果资产大小不能为负数")
        self.name = self.name.strip()

    def is_expired(self, now: datetime | None = None) -> bool:
        """An asset without an expiry never expires."""
        if self.expires_at is None:
            return False
        moment = now or _utcnow()
        return moment >= self.expires_at

    def belongs_to(self, context: AccessContext) -> bool:
        """Tenant is mandatory; ownership may be widened by privileged roles."""
        if self.tenant_id != context.tenant_id:
            return False
        if self.owner_id == context.user_id:
            return True
        return context.has_role(*CROSS_USER_ROLES)

    def provenance(self) -> dict[str, str]:
        """Traceability triplet: which step, which semantics, which data."""
        return {
            "source_step_id": self.source_step_id,
            "semantic_version": self.semantic_version,
            "data_version": self.data_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "owner_id": self.owner_id,
            "kind": self.kind,
            "name": self.name,
            "uri": self.uri,
            "source_step_id": self.source_step_id,
            "semantic_version": self.semantic_version,
            "data_version": self.data_version,
            "checksum": self.checksum,
            "size_bytes": self.size_bytes,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }
