"""Request fingerprinting and exactly-once admission decisions."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol
from uuid import uuid4

from app.domain.query_admission import AdmissionKey, QueryRequestAdmission
from app.schemas.queries import QueryRequest


class QueryAdmissionRepository(Protocol):
    def claim(self, admission: QueryRequestAdmission) -> tuple[QueryRequestAdmission, bool]: ...
    def get(self, key: AdmissionKey) -> QueryRequestAdmission | None: ...
    def find_by_task_id(self, *, tenant_id: str, task_id: str) -> QueryRequestAdmission | None: ...
    def update_status(self, key: AdmissionKey, status: str, error_code: str = "") -> QueryRequestAdmission: ...


class IdempotencyConflict(ValueError):
    code = "E_IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True)
class AdmissionResult:
    admission: QueryRequestAdmission
    created: bool


class QueryAdmissionService:
    def __init__(self, repository: QueryAdmissionRepository):
        self.repository = repository

    @staticmethod
    def request_sha256(request: QueryRequest) -> str:
        payload = request.model_dump(mode="json", exclude={"client_request_id"})
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    def admit(self, *, request: QueryRequest, tenant_id: str, owner_id: str) -> AdmissionResult:
        if not request.client_request_id:
            raise ValueError("E_IDEMPOTENCY_ID_REQUIRED:缺少 client_request_id")
        if request.branch_from_task_id:
            operation = "query.branch"
        else:
            operation = "query.follow_up" if request.follow_up else "query"
        admission = QueryRequestAdmission(
            tenant_id=tenant_id,
            owner_id=owner_id,
            operation=operation,
            client_request_id=request.client_request_id,
            request_sha256=self.request_sha256(request),
            task_id=request.task_id or str(uuid4()),
        )
        stored, created = self.repository.claim(admission)
        if stored.request_sha256 != admission.request_sha256:
            raise IdempotencyConflict(
                "E_IDEMPOTENCY_CONFLICT:同一 client_request_id 不能用于不同查询内容"
            )
        return AdmissionResult(admission=stored, created=created)

    def get(self, key: AdmissionKey) -> QueryRequestAdmission | None:
        return self.repository.get(key)

    def find_by_task_id(self, *, tenant_id: str, task_id: str) -> QueryRequestAdmission | None:
        return self.repository.find_by_task_id(tenant_id=tenant_id, task_id=task_id)

    def complete(self, admission: QueryRequestAdmission) -> QueryRequestAdmission:
        return self.repository.update_status(admission.key, "completed")

    def fail(self, admission: QueryRequestAdmission, *, error_code: str) -> QueryRequestAdmission:
        return self.repository.update_status(admission.key, "failed", error_code)


__all__ = [
    "AdmissionResult",
    "IdempotencyConflict",
    "QueryAdmissionRepository",
    "QueryAdmissionService",
]

