"""MySQL persistence for approval requests."""
import json
from datetime import datetime, timedelta, timezone
from .mysql_db import connect


def create_approval(approval: dict) -> dict:
    expires = datetime.now(timezone.utc) + timedelta(seconds=max(60, int(approval.get("ttl_seconds", 3600))))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO query_approvals (id,tenant_id,requester_id,task_id,question,sql_text,params_json,datasource_id,status,expires_at,reasons_json,trigger_type,analysis_mode) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)", (approval["id"], approval.get("tenant_id"), approval.get("requester_id") if str(approval.get("requester_id", "")).isdigit() else None, approval.get("task_id") or None, approval["question"], approval["sql"], json.dumps(approval.get("params", {})), approval["datasource_id"], expires, json.dumps(approval.get("reasons", [])), approval.get("trigger", "manual"), approval.get("analysis_mode", "none")))
        conn.commit()
    return {**approval, "expires_at": expires.timestamp()}


def _public(row):
    keys = ("id", "status", "question", "sql_text", "datasource_id", "created_at", "expires_at", "reasons_json", "trigger", "analysis_mode")
    item = dict(zip(keys, row))
    item["sql"] = item.pop("sql_text")
    item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
    for key in ("created_at", "expires_at"):
        if hasattr(item[key], "timestamp"): item[key] = item[key].timestamp()
    return item


def list_approvals(tenant_id: str) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE query_approvals SET status='expired' WHERE tenant_id=%s AND status='pending' AND expires_at < CURRENT_TIMESTAMP", (tenant_id,))
            cur.execute("SELECT id,status,question,sql_text,datasource_id,created_at,expires_at,reasons_json,trigger_type,analysis_mode FROM query_approvals WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 200", (tenant_id,))
            rows = cur.fetchall()
        conn.commit()
    return [_public(row) for row in rows]


def get_approval(approval_id: str, tenant_id: str) -> dict | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,status,question,sql_text,params_json,datasource_id,tenant_id,requester_id,task_id,created_at,expires_at,reasons_json,trigger_type,analysis_mode FROM query_approvals WHERE id=%s AND tenant_id=%s", (approval_id, tenant_id))
            row = cur.fetchone()
    if not row: return None
    keys = ("id", "status", "question", "sql", "params_json", "datasource_id", "tenant_id", "requester_id", "task_id", "created_at", "expires_at", "reasons_json", "trigger", "analysis_mode")
    item = dict(zip(keys, row)); item["params"] = json.loads(item.pop("params_json") or "{}"); item["reasons"] = json.loads(item.pop("reasons_json") or "[]")
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    if item["status"] == "pending" and item["expires_at"] <= utc_now: item["status"] = "expired"
    return item


def set_status(approval_id: str, tenant_id: str, status: str, expected: str = "pending") -> bool:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE query_approvals SET status=%s WHERE id=%s AND tenant_id=%s AND status=%s", (status, approval_id, tenant_id, expected))
            changed = cur.rowcount == 1
        conn.commit()
    return changed
