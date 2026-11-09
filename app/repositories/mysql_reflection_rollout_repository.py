"""MySQL persistence for tenant-scoped reflection rollout versions."""

from __future__ import annotations

from datetime import timezone

from app.domain.reflection_rollout import ReflectionRolloutPolicy, ReflectionRolloutVersion
from app.mysql_db import connect


class MySQLReflectionRolloutRepository:
    _columns = (
        "id,tenant_id,version,policy_json,state,canary_percent,created_by,created_at,updated_at"
    )

    @staticmethod
    def _record(row) -> ReflectionRolloutVersion:
        created_at = row[7].replace(tzinfo=timezone.utc) if row[7].tzinfo is None else row[7]
        updated_at = row[8].replace(tzinfo=timezone.utc) if row[8].tzinfo is None else row[8]
        return ReflectionRolloutVersion(
            id=row[0],
            tenant_id=row[1],
            policy=ReflectionRolloutPolicy.model_validate_json(row[3]),
            state=row[4],
            canary_percent=int(row[5]),
            created_by=row[6] or "",
            created_at=created_at,
            updated_at=updated_at,
        )

    def create(self, version: ReflectionRolloutVersion) -> ReflectionRolloutVersion:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO reflection_rollout_versions "
                    "(id,tenant_id,version,policy_json,state,canary_percent,created_by,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        version.id,
                        version.tenant_id,
                        version.policy.version,
                        version.policy.model_dump_json(),
                        version.state,
                        version.canary_percent,
                        version.created_by or None,
                        version.created_at.replace(tzinfo=None),
                        version.updated_at.replace(tzinfo=None),
                    ),
                )
            conn.commit()
        return version

    def get(self, version_id: str, tenant_id: str) -> ReflectionRolloutVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM reflection_rollout_versions WHERE id=%s AND tenant_id=%s",
                    (version_id, tenant_id),
                )
                row = cur.fetchone()
        return self._record(row) if row else None

    def list(self, tenant_id: str) -> list[ReflectionRolloutVersion]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM reflection_rollout_versions WHERE tenant_id=%s ORDER BY created_at DESC",
                    (tenant_id,),
                )
                rows = cur.fetchall()
        return [self._record(row) for row in rows]

    def set_canary(self, version_id: str, tenant_id: str, percentage: int) -> ReflectionRolloutVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM reflection_rollout_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
                    (version_id, tenant_id),
                )
                if cur.fetchone() is None:
                    return None
                cur.execute(
                    "UPDATE reflection_rollout_versions SET state='retired',canary_percent=0 "
                    "WHERE tenant_id=%s AND state='canary' AND id<>%s",
                    (tenant_id, version_id),
                )
                cur.execute(
                    "UPDATE reflection_rollout_versions SET state='canary',canary_percent=%s "
                    "WHERE id=%s AND tenant_id=%s",
                    (percentage, version_id, tenant_id),
                )
            conn.commit()
        return self.get(version_id, tenant_id)

    def activate(self, version_id: str, tenant_id: str) -> ReflectionRolloutVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM reflection_rollout_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
                    (version_id, tenant_id),
                )
                if cur.fetchone() is None:
                    return None
                cur.execute(
                    "UPDATE reflection_rollout_versions SET state='retired',canary_percent=0 "
                    "WHERE tenant_id=%s AND state IN ('active','canary') AND id<>%s",
                    (tenant_id, version_id),
                )
                cur.execute(
                    "UPDATE reflection_rollout_versions SET state='active',canary_percent=0 "
                    "WHERE id=%s AND tenant_id=%s",
                    (version_id, tenant_id),
                )
            conn.commit()
        return self.get(version_id, tenant_id)

    def retire(self, version_id: str, tenant_id: str) -> ReflectionRolloutVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reflection_rollout_versions SET state='retired',canary_percent=0 "
                    "WHERE id=%s AND tenant_id=%s",
                    (version_id, tenant_id),
                )
                if cur.rowcount == 0:
                    return None
            conn.commit()
        return self.get(version_id, tenant_id)


__all__ = ["MySQLReflectionRolloutRepository"]

