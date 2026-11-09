import json
from datetime import datetime, timezone
from .mysql_db import connect

def create_job(job_id, user_id, tenant_id, datasource_id, sql, params, expires_at):
    with connect() as c:
        with c.cursor() as cur:
            cur.execute("INSERT INTO export_jobs (id,user_id,tenant_id,datasource_id,status,sql_text,params_json,expires_at) VALUES (%s,%s,%s,%s,'queued',%s,%s,%s)", (job_id,user_id,tenant_id,datasource_id,sql,json.dumps(params),expires_at))
        c.commit()

def update_job(job_id, **fields):
    allowed = {"status","file_path","row_count","error_message"}; fields = {k:v for k,v in fields.items() if k in allowed}
    if not fields: return
    with connect() as c:
        with c.cursor() as cur:
            cur.execute(f"UPDATE export_jobs SET {', '.join(f'{k}=%s' for k in fields)} WHERE id=%s", (*fields.values(), job_id))
        c.commit()

def get_job(job_id, user_id, tenant_id):
    with connect() as c:
        with c.cursor() as cur:
            cur.execute("SELECT status,file_path,row_count,error_message,created_at,expires_at,user_id,tenant_id FROM export_jobs WHERE id=%s AND tenant_id=%s AND (user_id=%s OR user_id IS NULL)", (job_id,tenant_id,user_id))
            row=cur.fetchone()
    if not row: return None
    return dict(zip(("status","path","row_count","error","created_at","expires_at","user_id","tenant_id"),row))

def get_job_payload(job_id, tenant_id=None):
    """Load the execution payload for a worker, scoped to the tenant."""
    with connect() as c:
        with c.cursor() as cur:
            cur.execute("SELECT status,sql_text,params_json,datasource_id,tenant_id,user_id FROM export_jobs WHERE id=%s AND (%s IS NULL OR tenant_id=%s)", (job_id, tenant_id, tenant_id))
            row = cur.fetchone()
    if not row:
        return None
    status, sql_text, params_json, datasource_id, row_tenant, user_id = row
    return {"status": status, "sql": sql_text, "params": json.loads(params_json or "{}"), "datasource_id": datasource_id, "tenant_id": row_tenant, "user_id": user_id}

def delete_expired():
    with connect() as c:
        with c.cursor() as cur: cur.execute("DELETE FROM export_jobs WHERE expires_at < CURRENT_TIMESTAMP")
        c.commit()

def recover_stale_jobs(max_age_seconds=900):
    """Requeue jobs left running after a worker/API crash."""
    with connect() as c:
        with c.cursor() as cur:
            cur.execute("UPDATE export_jobs SET status='queued', error_message='worker restart recovery' WHERE status='running' AND updated_at < (CURRENT_TIMESTAMP - INTERVAL %s SECOND)", (max_age_seconds,))
            count = cur.rowcount
        c.commit()
    return count
