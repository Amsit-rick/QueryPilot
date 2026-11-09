"""MySQL persistence for governed Skill versions."""

from __future__ import annotations

from datetime import timezone

from app.domain.skill import SkillDefinition, SkillVersion
from app.mysql_db import connect


class MySQLSkillRepository:
    _columns = (
        "id,tenant_id,skill_id,version,definition_json,state,canary_percent,"
        "created_by,created_at,updated_at"
    )

    @staticmethod
    def _record(row) -> SkillVersion:
        created_at = row[8].replace(tzinfo=timezone.utc) if row[8].tzinfo is None else row[8]
        updated_at = row[9].replace(tzinfo=timezone.utc) if row[9].tzinfo is None else row[9]
        return SkillVersion(
            id=row[0],
            tenant_id=row[1],
            definition=SkillDefinition.model_validate_json(row[4]),
            state=row[5],
            canary_percent=int(row[6]),
            created_by=row[7] or "",
            created_at=created_at,
            updated_at=updated_at,
        )

    def create(self, version: SkillVersion) -> SkillVersion:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO skill_versions
                      (id,tenant_id,skill_id,version,definition_json,state,
                       canary_percent,created_by,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        version.id,
                        version.tenant_id,
                        version.definition.id,
                        version.definition.version,
                        version.definition.model_dump_json(),
                        version.state,
                        version.canary_percent,
                        version.created_by or None,
                        version.created_at.replace(tzinfo=None),
                        version.updated_at.replace(tzinfo=None),
                    ),
                )
            conn.commit()
        return version

    def get(self, version_id: str, tenant_id: str) -> SkillVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM skill_versions WHERE id=%s AND tenant_id=%s",
                    (version_id, tenant_id),
                )
                row = cur.fetchone()
        return self._record(row) if row else None

    def list(self, tenant_id: str, skill_id: str = "") -> list[SkillVersion]:
        sql = f"SELECT {self._columns} FROM skill_versions WHERE tenant_id=%s"
        params: list[str] = [tenant_id]
        if skill_id:
            sql += " AND skill_id=%s"
            params.append(skill_id)
        sql += " ORDER BY created_at DESC"
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        return [self._record(row) for row in rows]

    def set_canary(
        self, version_id: str, tenant_id: str, percentage: int
    ) -> SkillVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM skill_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
                    (version_id, tenant_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                target = self._record(row)
                cur.execute(
                    "UPDATE skill_versions SET state='retired',canary_percent=0 "
                    "WHERE tenant_id=%s AND skill_id=%s AND state='canary' AND id<>%s",
                    (tenant_id, target.definition.id, version_id),
                )
                cur.execute(
                    "UPDATE skill_versions SET state='canary',canary_percent=%s "
                    "WHERE id=%s AND tenant_id=%s",
                    (percentage, version_id, tenant_id),
                )
            conn.commit()
        return self.get(version_id, tenant_id)

    def activate(self, version_id: str, tenant_id: str) -> SkillVersion | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {self._columns} FROM skill_versions WHERE id=%s AND tenant_id=%s FOR UPDATE",
                    (version_id, tenant_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                target = self._record(row)
                cur.execute(
                    "UPDATE skill_versions SET state='retired',canary_percent=0 "
                    "WHERE tenant_id=%s AND skill_id=%s AND state IN ('active','canary') AND id<>%s",
                    (tenant_id, target.definition.id, version_id),
                )
                cur.execute(
                    "UPDATE skill_versions SET state='active',canary_percent=0 "
                    "WHERE id=%s AND tenant_id=%s",
                    (version_id, tenant_id),
                )
            conn.commit()
        return self.get(version_id, tenant_id)


__all__ = ["MySQLSkillRepository"]
