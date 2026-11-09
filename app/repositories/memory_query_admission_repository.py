"""Thread-safe in-memory query admission repository for local development."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock

from app.domain.query_admission import AdmissionKey, QueryRequestAdmission


class InMemoryQueryAdmissionRepository:
    def __init__(self):
        self._records: dict[AdmissionKey, QueryRequestAdmission] = {}
        self._lock = Lock()

    def claim(self, admission: QueryRequestAdmission) -> tuple[QueryRequestAdmission, bool]:
        with self._lock:
            existing = self._records.get(admission.key)
            if existing is not None:
                return replace(existing), False
            self._records[admission.key] = replace(admission)
            return replace(admission), True

    def get(self, key: AdmissionKey) -> QueryRequestAdmission | None:
        with self._lock:
            admission = self._records.get(key)
            return replace(admission) if admission is not None else None

    def find_by_task_id(self, *, tenant_id: str, task_id: str) -> QueryRequestAdmission | None:
        with self._lock:
            matches = [
                admission
                for admission in self._records.values()
                if admission.tenant_id == tenant_id and admission.task_id == task_id
            ]
            if not matches:
                return None
            latest = max(matches, key=lambda admission: admission.updated_at)
            return replace(latest)

    def update_status(self, key: AdmissionKey, status: str, error_code: str = "") -> QueryRequestAdmission:
        with self._lock:
            admission = self._records.get(key)
            if admission is None:
                raise LookupError("E_IDEMPOTENCY_NOT_FOUND:请求准入记录不存在")
            admission.status = status
            admission.error_code = error_code
            admission.updated_at = datetime.now(timezone.utc)
            return replace(admission)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


__all__ = ["InMemoryQueryAdmissionRepository"]

