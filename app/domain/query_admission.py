"""Durable admission record for one client query request."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


AdmissionKey = tuple[str, str, str, str]


@dataclass
class QueryRequestAdmission:
    tenant_id: str
    owner_id: str
    operation: str
    client_request_id: str
    request_sha256: str
    task_id: str
    status: str = "running"
    error_code: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> AdmissionKey:
        return (self.tenant_id, self.owner_id, self.operation, self.client_request_id)


__all__ = ["AdmissionKey", "QueryRequestAdmission"]

