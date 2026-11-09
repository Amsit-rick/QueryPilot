"""MySQL persistence adapter for DataTask entities."""
from __future__ import annotations

import json
from app.domain.task import DataTask, TaskArtifact, TaskEvent, TaskStep
from app.mysql_db import connect


_TASK_COLUMNS = (
    "id,tenant_id,owner_id,datasource_id,parent_task_id,root_task_id,"
    "branch_source_trace_id,branch_label,branch_depth,title,question,status,created_at,updated_at"
)


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    return json.loads(value)


def _task_from_row(row) -> DataTask:
    return DataTask(
        id=row[0],
        tenant_id=row[1],
        owner_id=row[2],
        datasource_id=row[3],
        parent_task_id=row[4] or "",
        root_task_id=row[5] or "",
        branch_source_trace_id=row[6] or "",
        branch_label=row[7] or "",
        branch_depth=int(row[8] or 0),
        title=row[9],
        question=row[10],
        status=row[11],
        created_at=row[12],
        updated_at=row[13],
    )


class MySQLTaskRepository:
    def create(self, task: DataTask) -> DataTask:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO data_tasks "
                    "(id,tenant_id,owner_id,datasource_id,parent_task_id,root_task_id,branch_source_trace_id,branch_label,branch_depth,title,question,status,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        task.id,
                        task.tenant_id,
                        task.owner_id,
                        task.datasource_id,
                        task.parent_task_id or None,
                        task.root_task_id or None,
                        task.branch_source_trace_id or None,
                        task.branch_label,
                        task.branch_depth,
                        task.display_title(),
                        task.question,
                        task.status,
                        task.created_at.replace(tzinfo=None),
                    ),
                )
            conn.commit()
        return task

    def get(self, task_id: str, tenant_id: str) -> DataTask | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_TASK_COLUMNS} FROM data_tasks WHERE id=%s AND tenant_id=%s", (task_id, tenant_id))
                row = cur.fetchone()
        if not row:
            return None
        return _task_from_row(row)

    def update(self, task: DataTask) -> DataTask:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE data_tasks SET status=%s,title=%s WHERE id=%s AND tenant_id=%s", (task.status, task.display_title(), task.id, task.tenant_id))
            conn.commit()
        return task

    def list(self, tenant_id: str, owner_id: str, limit: int = 20) -> list[DataTask]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_TASK_COLUMNS} FROM data_tasks WHERE tenant_id=%s AND owner_id=%s ORDER BY updated_at DESC LIMIT %s", (tenant_id, owner_id, max(1, min(limit, 100))))
                rows = cur.fetchall()
        return [_task_from_row(row) for row in rows]

    def list_branches(self, *, parent_task_id, tenant_id, owner_id) -> list[DataTask]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_TASK_COLUMNS} FROM data_tasks "
                    "WHERE tenant_id=%s AND owner_id=%s AND parent_task_id=%s "
                    "ORDER BY created_at,id",
                    (tenant_id, owner_id, parent_task_id),
                )
                rows = cur.fetchall()
        return [_task_from_row(row) for row in rows]

    def list_branch_family(self, *, root_task_id, tenant_id, owner_id) -> list[DataTask]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_TASK_COLUMNS} FROM data_tasks "
                    "WHERE tenant_id=%s AND owner_id=%s AND (id=%s OR root_task_id=%s) "
                    "ORDER BY branch_depth,created_at,id",
                    (tenant_id, owner_id, root_task_id, root_task_id),
                )
                rows = cur.fetchall()
        return [_task_from_row(row) for row in rows]

    def list_recoverable(self, *, statuses, updated_before, limit) -> list[DataTask]:
        safe_statuses = tuple(dict.fromkeys(str(status) for status in statuses if str(status)))
        if not safe_statuses:
            return []
        safe_limit = max(1, min(int(limit), 500))
        placeholders = ",".join(["%s"] * len(safe_statuses))
        sql = (
            f"SELECT {_TASK_COLUMNS} "
            f"FROM data_tasks WHERE status IN ({placeholders}) AND updated_at<=%s "
            "ORDER BY updated_at,id LIMIT %s"
        )
        cutoff = updated_before.replace(tzinfo=None) if updated_before.tzinfo is not None else updated_before
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (*safe_statuses, cutoff, safe_limit))
                rows = cur.fetchall()
        return [_task_from_row(row) for row in rows]

    def claim_recovery_fence(self, *, task_id, tenant_id, fencing_token) -> bool:
        token = int(fencing_token)
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data_tasks SET recovery_fencing_token=%s,updated_at=updated_at "
                    "WHERE id=%s AND tenant_id=%s AND recovery_fencing_token<%s",
                    (token, task_id, tenant_id, token),
                )
                claimed = cur.rowcount == 1
            conn.commit()
        return claimed

    def update_recovery_status(self, *, task, fencing_token) -> DataTask | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE data_tasks SET status=%s,title=%s "
                    "WHERE id=%s AND tenant_id=%s AND recovery_fencing_token=%s",
                    (
                        task.status,
                        task.display_title(),
                        task.id,
                        task.tenant_id,
                        int(fencing_token),
                    ),
                )
                updated = cur.rowcount == 1
            conn.commit()
        return task if updated else None

    def add_recovery_event(
        self,
        *,
        task_id,
        tenant_id,
        fencing_token,
        idempotency_key,
        payload,
    ) -> bool:
        event_payload = json.dumps(
            {**payload, "idempotency_key": idempotency_key},
            ensure_ascii=False,
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO data_task_events (task_id,kind,payload_json) "
                    "SELECT t.id,%s,%s FROM data_tasks t "
                    "WHERE t.id=%s AND t.tenant_id=%s AND t.recovery_fencing_token=%s "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM data_task_events e WHERE e.task_id=t.id AND e.kind=%s "
                    "AND JSON_UNQUOTE(JSON_EXTRACT(e.payload_json,'$.idempotency_key'))=%s"
                    ")",
                    (
                        "recovery_supervisor",
                        event_payload,
                        task_id,
                        tenant_id,
                        int(fencing_token),
                        "recovery_supervisor",
                        idempotency_key,
                    ),
                )
                inserted = cur.rowcount == 1
            conn.commit()
        return inserted

    def add_step(self, step: TaskStep) -> TaskStep:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO data_task_steps (task_id,sequence_no,kind,status,input_json,output_json,error_text,duration_ms,retry_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (step.task_id, step.sequence, step.kind, step.status, json.dumps(step.input, ensure_ascii=False), json.dumps(step.output, ensure_ascii=False), step.error or None, step.duration_ms, step.retry_count))
                step.id = cur.lastrowid
            conn.commit()
        return step

    def list_steps(self, task_id: str) -> list[TaskStep]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,task_id,sequence_no,kind,status,input_json,output_json,error_text,duration_ms,retry_count,created_at FROM data_task_steps WHERE task_id=%s ORDER BY sequence_no", (task_id,))
                rows = cur.fetchall()
        return [TaskStep(id=r[0], task_id=r[1], sequence=r[2], kind=r[3], status=r[4], input=_json_object(r[5]), output=_json_object(r[6]), error=r[7] or "", duration_ms=r[8], retry_count=r[9], created_at=r[10]) for r in rows]

    def update_latest_step(self, task_id: str, kind: str, status: str, output: dict, error: str = "") -> TaskStep | None:
        steps = [step for step in self.list_steps(task_id) if step.kind == kind]
        if not steps:
            return None
        step = steps[-1]
        step.status = status
        step.output = {**step.output, **output}
        step.error = error
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE data_task_steps SET status=%s,output_json=%s,error_text=%s WHERE id=%s", (status, json.dumps(step.output, ensure_ascii=False), error or None, step.id))
            conn.commit()
        return step

    def add_event(self, event: TaskEvent) -> TaskEvent:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO data_task_events (task_id,kind,payload_json) VALUES (%s,%s,%s)", (event.task_id, event.kind, json.dumps(event.payload, ensure_ascii=False)))
                event.id = cur.lastrowid
            conn.commit()
        return event

    def list_events(self, task_id: str) -> list[TaskEvent]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,task_id,kind,payload_json,created_at FROM data_task_events WHERE task_id=%s ORDER BY id", (task_id,))
                rows = cur.fetchall()
        return [TaskEvent(id=r[0], task_id=r[1], kind=r[2], payload=_json_object(r[3]), created_at=r[4]) for r in rows]

    def add_artifact(self, artifact: TaskArtifact) -> TaskArtifact:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO data_task_artifacts (id,task_id,kind,name,uri,metadata_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (artifact.id, artifact.task_id, artifact.kind, artifact.name, artifact.uri, json.dumps(artifact.metadata, ensure_ascii=False), artifact.created_at.replace(tzinfo=None)))
            conn.commit()
        return artifact

    def list_artifacts(self, task_id: str) -> list[TaskArtifact]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id,task_id,kind,name,uri,metadata_json,created_at FROM data_task_artifacts WHERE task_id=%s ORDER BY created_at,id", (task_id,))
                rows = cur.fetchall()
        return [TaskArtifact(id=r[0], task_id=r[1], kind=r[2], name=r[3], uri=r[4], metadata=_json_object(r[5]), created_at=r[6]) for r in rows]

    def list_artifacts_for_tenant(self, tenant_id: str, kind: str, limit: int = 1000) -> list[TaskArtifact]:
        safe_limit = max(1, min(limit, 10000))
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT a.id,a.task_id,a.kind,a.name,a.uri,a.metadata_json,a.created_at "
                    "FROM data_task_artifacts a JOIN data_tasks t ON t.id=a.task_id "
                    "WHERE t.tenant_id=%s AND a.kind=%s ORDER BY a.created_at,a.id LIMIT %s",
                    (tenant_id, kind, safe_limit),
                )
                rows = cur.fetchall()
        return [TaskArtifact(id=r[0], task_id=r[1], kind=r[2], name=r[3], uri=r[4], metadata=_json_object(r[5]), created_at=r[6]) for r in rows]
