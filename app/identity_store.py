"""Parameterized MySQL persistence for production identities."""
from datetime import datetime
import json
import uuid
from .mysql_db import connect


def get_user(username: str) -> dict | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, password_hash, role, tenant_id, is_active, failed_attempts, locked_until FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
    if not row:
        return None
    keys = ("id", "username", "password_hash", "role", "tenant_id", "is_active", "failed_attempts", "locked_until")
    return dict(zip(keys, row))

def get_user_by_id(user_id: int) -> dict | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, password_hash, role, tenant_id, is_active, failed_attempts, locked_until FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
    if not row: return None
    return dict(zip(("id", "username", "password_hash", "role", "tenant_id", "is_active", "failed_attempts", "locked_until"), row))


def record_session(session_id: str, user_id: int, token_hash: str, expires_at: datetime, ip_address: str | None = None, user_agent: str | None = None) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_sessions (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent, last_active_at) VALUES (%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)",
                (session_id, user_id, token_hash, expires_at, ip_address, (user_agent or "")[:512]),
            )
        conn.commit()


def revoke_session(session_id: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=%s", (session_id,))
        conn.commit()


def revoke_session_by_id(user_id: int, session_id: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=%s AND user_id=%s AND revoked_at IS NULL", (session_id, user_id))
        conn.commit()


def list_sessions(user_id: int) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, created_at, last_active_at, expires_at, ip_address, user_agent FROM user_sessions WHERE user_id=%s AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP ORDER BY last_active_at DESC",
                (user_id,),
            )
            rows = cur.fetchall()
    keys = ("id", "created_at", "last_active_at", "expires_at", "ip_address", "user_agent")
    result = []
    for row in rows:
        item = dict(zip(keys, row))
        item["created_at"] = item["created_at"].isoformat() if item["created_at"] else None
        item["last_active_at"] = item["last_active_at"].isoformat() if item["last_active_at"] else None
        item["expires_at"] = item["expires_at"].isoformat() if item["expires_at"] else None
        result.append(item)
    return result


def get_user_profile(user_id: int) -> dict | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, display_name, role, tenant_id, is_active, locked_until, created_at FROM users WHERE id=%s", (user_id,))
            row = cur.fetchone()
    if not row:
        return None
    keys = ("id", "username", "display_name", "role", "tenant_id", "is_active", "locked_until", "created_at")
    item = dict(zip(keys, row))
    item["created_at"] = item["created_at"].isoformat() if item["created_at"] else None
    item["locked_until"] = item["locked_until"].isoformat() if item["locked_until"] else None
    return item


def update_profile(user_id: int, display_name: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET display_name=%s WHERE id=%s", (display_name, user_id))
        conn.commit()


def get_user_grants(user_id: int) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, datasource_id, scope_column, scope_value FROM user_data_grants WHERE user_id=%s ORDER BY id", (user_id,))
            rows = cur.fetchall()
    return [dict(zip(("id", "datasource_id", "scope_column", "scope_value"), row)) for row in rows]


def add_user_grant(user_id: int, datasource_id: str, scope_column: str, scope_value: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_data_grants (user_id, datasource_id, scope_column, scope_value) VALUES (%s,%s,%s,%s)",
                (user_id, datasource_id, scope_column, scope_value),
            )
            grant_id = cur.lastrowid
        conn.commit()
    return int(grant_id)


def delete_user_grant(grant_id: int, tenant_id: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE g FROM user_data_grants g JOIN users u ON u.id=g.user_id WHERE g.id=%s AND u.tenant_id=%s",
                (grant_id, tenant_id),
            )
        conn.commit()


def update_login_failure(user_id: int, attempts: int, locked_until: datetime | None = None) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET failed_attempts=%s, locked_until=%s WHERE id=%s", (attempts, locked_until, user_id))
        conn.commit()


def clear_login_failures(user_id: int) -> None:
    update_login_failure(user_id, 0, None)


def get_session(session_id: str, token_hash: str) -> dict | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, expires_at, revoked_at, ip_address, user_agent FROM user_sessions WHERE id=%s AND refresh_token_hash=%s", (session_id, token_hash))
            row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("user_id", "expires_at", "revoked_at", "ip_address", "user_agent"), row))


def save_query(user_id: int, tenant_id: str, question: str, sql: str, result: dict) -> str:
    session_id = str(uuid.uuid4())
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO query_sessions (id,user_id,tenant_id,title) VALUES (%s,%s,%s,%s)", (session_id, user_id, tenant_id, question[:255]))
            cur.execute("INSERT INTO query_messages (session_id,user_id,tenant_id,question,sql_text,result_json,row_count,trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (session_id, user_id, tenant_id, question, sql, json.dumps(result, ensure_ascii=False), result.get("row_count", 0), result.get("trace_id", "")))
        conn.commit()
    return session_id


def list_queries(user_id: int, tenant_id: str) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT s.id,s.title,s.updated_at,m.question,m.sql_text,m.result_json,m.trace_id FROM query_sessions s JOIN query_messages m ON m.session_id=s.id WHERE s.user_id=%s AND s.tenant_id=%s ORDER BY s.updated_at DESC LIMIT 20", (user_id, tenant_id))
            rows = cur.fetchall()
    return [{"session_id": r[0], "title": r[1], "updated_at": r[2].isoformat() if r[2] else None, "question": r[3], "sql": r[4], "result": json.loads(r[5]) if r[5] else None, "trace_id": r[6]} for r in rows]


def delete_query(user_id: int, tenant_id: str, session_id: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM query_sessions WHERE id=%s AND user_id=%s AND tenant_id=%s", (session_id, user_id, tenant_id))
        conn.commit()


def revoke_user_sessions(user_id: int) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
        conn.commit()


def update_password(user_id: int, password_hash: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash=%s, failed_attempts=0, locked_until=NULL WHERE id=%s", (password_hash, user_id))
        conn.commit()

def list_users(tenant_id: str) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,username,role,tenant_id,is_active,failed_attempts,locked_until,created_at FROM users WHERE tenant_id=%s ORDER BY id", (tenant_id,))
            rows = cur.fetchall()
    keys = ("id", "username", "role", "tenant_id", "is_active", "failed_attempts", "locked_until", "created_at")
    return [{**dict(zip(keys, row)), "created_at": row[7].isoformat() if row[7] else None} for row in rows]

def create_user(username: str, password_hash: str, role: str, tenant_id: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users(username,password_hash,role,tenant_id) VALUES(%s,%s,%s,%s)", (username, password_hash, role, tenant_id))
            user_id = cur.lastrowid
        conn.commit()
    return int(user_id)

def set_user_status(user_id: int, tenant_id: str, active: bool) -> None:
    with connect() as conn:
        with conn.cursor() as cur: cur.execute("UPDATE users SET is_active=%s WHERE id=%s AND tenant_id=%s", (active, user_id, tenant_id))
        conn.commit()

def set_user_role(user_id: int, tenant_id: str, role: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur: cur.execute("UPDATE users SET role=%s WHERE id=%s AND tenant_id=%s", (role, user_id, tenant_id))
        conn.commit()

def delete_user(user_id: int, tenant_id: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur: cur.execute("DELETE FROM users WHERE id=%s AND tenant_id=%s", (user_id, tenant_id))
        conn.commit()
