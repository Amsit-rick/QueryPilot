"""MySQL persistence for atomic query request admission."""
from __future__ import annotations

from datetime import timezone

import pymysql

from app.domain.query_admission import AdmissionKey, QueryRequestAdmission
from app.mysql_db import connect


class MySQLQueryAdmissionRepository:
    _columns = (
        "tenant_id,owner_id,operation,client_request_id,request_sha256,task_id,"
        "status,error_code,created_at,updated_at"
    )

    @staticmethod
    def _record(row) -> QueryRequestAdmission:
        created_at = row[8].replace(tzinfo=timezone.utc) if row[8].tzinfo is None else row[8]
        updated_at = row[9].replace(tzinfo=timezone.utc) if row[9].tzinfo is None else row[9]
        return QueryRequestAdmission(
            tenant_id=row[0],
            owner_id=row[1],
            operation=row[2],
            client_request_id=row[3],
            request_sha256=row[4],
            task_id=row[5],
            status=row[6],
            error_code=row[7] or "",
            created_at=created_at,
            updated_at=updated_at,
        )

    def claim(self, admission: QueryRequestAdmission) -> tuple[QueryRequestAdmission, bool]:
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO query_request_admissions "
                        "(tenant_id,owner_id,operation,client_request_id,request_sha256,task_id,status,error_code,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            admission.tenant_id,
                            admission.owner_id,
                            admission.operation,
                            admission.client_request_id,
                            admission.request_sha256,
                            admission.task_id,
                            admission.status,
                            admission.error_code or None,
                            admission.created_at.replace(tzinfo=None),
                            admission.updated_at.replace(tzinfo=None),
                        ),
                    )
                conn.commit()
            return admission, True
        except pymysql.err.IntegrityError as exc:
            if not exc.args or exc.args[0] != 1062:
                raise
            existing = self.get(admission.key)
            if existing is None:
                raise RuntimeError("E_IDEMPOTENCY_RACE:请求准入冲突后未找到记录") from exc
            return existing, False

    def get(self, key: AdmissionKey) -> QueryRequestAdmission | None:
        tenant_id, owner_id, operation, client_request_id = key
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM query_request_admissions "
                    "WHERE tenant_id=%s AND owner_id=%s AND operation=%s AND client_request_id=%s",
                    (tenant_id, owner_id, operation, client_request_id),
                )
                row = cur.fetchone()
        return self._record(row) if row else None

    def find_by_task_id(self, *, tenant_id: str, task_id: str) -> QueryRequestAdmission | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM query_request_admissions "
                    "WHERE tenant_id=%s AND task_id=%s ORDER BY updated_at DESC LIMIT 1",
                    (tenant_id, task_id),
                )
                row = cur.fetchone()
        return self._record(row) if row else None

    def update_status(self, key: AdmissionKey, status: str, error_code: str = "") -> QueryRequestAdmission:
        tenant_id, owner_id, operation, client_request_id = key
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE query_request_admissions SET status=%s,error_code=%s "
                    "WHERE tenant_id=%s AND owner_id=%s AND operation=%s AND client_request_id=%s",
                    (status, error_code or None, tenant_id, owner_id, operation, client_request_id),
                )
                if cur.rowcount == 0:
                    raise LookupError("E_IDEMPOTENCY_NOT_FOUND:请求准入记录不存在")
            conn.commit()
        updated = self.get(key)
        if updated is None:
            raise LookupError("E_IDEMPOTENCY_NOT_FOUND:请求准入记录不存在")
        return updated


__all__ = ["MySQLQueryAdmissionRepository"]

