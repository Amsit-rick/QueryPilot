"""Endpoint-level tests for authentication & authorization contracts.

The MySQL persistence layer is mocked so these run without a database while
still exercising the FastAPI routes end-to-end (status codes, error contract,
and which store functions are called with which arguments).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.auth import require_identity


@pytest.fixture()
def client():
    app = main_module.app
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _override(identity):
    main_module.app.dependency_overrides[require_identity] = lambda: identity


def _active_user():
    return {
        "id": 1,
        "username": "alice",
        "display_name": "Alice",
        "password_hash": "h",
        "role": "admin",
        "tenant_id": "default",
        "is_active": True,
        "failed_attempts": 0,
        "locked_until": None,
    }


def _mock_tokens(monkeypatch, **claims_overrides):
    tokens = {"access_token": "a", "refresh_token": "r", "token_type": "bearer", "session_id": "s1"}
    monkeypatch.setattr(main_module, "issue_tokens", lambda **kw: tokens)
    monkeypatch.setattr(main_module, "refresh_token_hash", lambda token: "hash")
    monkeypatch.setattr(main_module, "record_session", lambda *a, **kw: None)
    monkeypatch.setattr(main_module, "revoke_session", lambda sid: None)
    return tokens


# ===== 登录接口测试 =====

def test_login_success(client, monkeypatch):
    _mock_tokens(monkeypatch)
    monkeypatch.setattr(main_module, "get_user", lambda username: _active_user())
    monkeypatch.setattr(main_module, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(main_module, "clear_login_failures", lambda user_id: None)

    resp = client.post("/api/v1/auth/login", json={"username": "alice", "password": "secret123"})

    assert resp.status_code == 200
    assert resp.json()["access_token"] == "a"
    assert resp.json()["session_id"] == "s1"


def test_login_wrong_password_returns_401(client, monkeypatch):
    monkeypatch.setattr(main_module, "get_user", lambda username: _active_user())
    monkeypatch.setattr(main_module, "verify_password", lambda plain, hashed: False)
    calls = []
    monkeypatch.setattr(main_module, "update_login_failure", lambda user_id, attempts, lock: calls.append((user_id, attempts, lock)))

    resp = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"})

    assert resp.status_code == 401
    assert calls == [(1, 1, None)]


def test_login_locked_account_returns_423(client, monkeypatch):
    user = _active_user()
    user["locked_until"] = datetime.now(timezone.utc) + timedelta(minutes=10)
    monkeypatch.setattr(main_module, "get_user", lambda username: user)

    resp = client.post("/api/v1/auth/login", json={"username": "alice", "password": "secret123"})

    assert resp.status_code == 423


def test_login_inactive_account_returns_401(client, monkeypatch):
    user = _active_user()
    user["is_active"] = False
    monkeypatch.setattr(main_module, "get_user", lambda username: user)

    resp = client.post("/api/v1/auth/login", json={"username": "alice", "password": "secret123"})

    assert resp.status_code == 401


# ===== 修改密码测试 =====

def test_change_password_success(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "get_user_by_id", lambda user_id: {"id": 1, "password_hash": "h"})
    monkeypatch.setattr(main_module, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(main_module, "hash_password", lambda p: "new-hash")
    calls = []
    monkeypatch.setattr(main_module, "update_password", lambda user_id, ph: calls.append(("update", user_id, ph)))
    monkeypatch.setattr(main_module, "revoke_user_sessions", lambda user_id: calls.append(("revoke", user_id)))

    resp = client.post("/api/v1/auth/change-password", json={"current_password": "old-password", "new_password": "new-password-123"})

    assert resp.status_code == 200
    assert calls == [("update", 1, "new-hash"), ("revoke", 1)]


def test_change_password_wrong_current_returns_401(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "get_user_by_id", lambda user_id: {"id": 1, "password_hash": "h"})
    monkeypatch.setattr(main_module, "verify_password", lambda plain, hashed: False)

    assert client.post("/api/v1/auth/change-password", json={"current_password": "wrong", "new_password": "new-password-123"}).status_code == 401


def test_change_password_short_new_password_returns_422(client):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"]})
    assert client.post("/api/v1/auth/change-password", json={"current_password": "old-password", "new_password": "short"}).status_code == 422


# ===== Token 刷新测试 =====

def test_refresh_success_rotates_session(client, monkeypatch):
    tokens = _mock_tokens(monkeypatch)
    monkeypatch.setattr(main_module, "decode_refresh_claims", lambda token: {"type": "refresh", "sid": "s1", "sub": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "get_session", lambda sid, token_hash: {"user_id": 1, "expires_at": datetime.now() + timedelta(days=1), "revoked_at": None, "ip_address": None, "user_agent": None})
    revoked = []
    monkeypatch.setattr(main_module, "revoke_session", lambda sid: revoked.append(sid))

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "r" * 20})

    assert resp.status_code == 200
    assert resp.json()["access_token"] == "a"
    assert revoked == ["s1"]

def test_refresh_accepts_cookie_only_request(client, monkeypatch):
    _mock_tokens(monkeypatch)
    monkeypatch.setattr(main_module, "decode_refresh_claims", lambda token: {"type": "refresh", "sid": "s1", "sub": "1", "tenant_id": "default", "roles": ["admin"]})
    monkeypatch.setattr(main_module, "get_session", lambda sid, token_hash: {"user_id": 1, "expires_at": datetime.now() + timedelta(days=1), "revoked_at": None, "ip_address": None, "user_agent": None})
    monkeypatch.setattr(main_module, "revoke_session", lambda sid: None)
    resp = client.post("/api/v1/auth/refresh", cookies={"query_pilot_refresh": "r" * 20})
    assert resp.status_code == 200


def test_refresh_revoked_session_returns_401(client, monkeypatch):
    _mock_tokens(monkeypatch)
    monkeypatch.setattr(main_module, "decode_refresh_claims", lambda token: {"type": "refresh", "sid": "s1", "sub": "1"})
    monkeypatch.setattr(main_module, "get_session", lambda sid, token_hash: {"user_id": 1, "expires_at": datetime.now() + timedelta(days=1), "revoked_at": datetime.now(), "ip_address": None, "user_agent": None})

    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "r" * 20}).status_code == 401


def test_refresh_invalid_token_returns_401(client, monkeypatch):
    def raise_on_decode(token):
        raise Exception("bad token")
    monkeypatch.setattr(main_module, "decode_refresh_claims", raise_on_decode)

    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "b" * 20}).status_code == 401


# ===== 会话撤销测试 =====

def test_logout_revokes_session(client, monkeypatch):
    monkeypatch.setattr(main_module, "decode_refresh_claims", lambda token: {"sid": "s1"})
    revoked = []
    monkeypatch.setattr(main_module, "revoke_session", lambda sid: revoked.append(sid))

    resp = client.post("/api/v1/auth/logout", json={"refresh_token": "r" * 20})

    assert resp.status_code == 200
    assert revoked == ["s1"]


# ===== RBAC API 测试 =====

def test_query_requires_query_permission(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["nonexistent"]})
    resp = client.post("/api/v1/query", json={"question": "查询", "sql": "SELECT region FROM sales"})
    assert resp.status_code == 403


def test_export_requires_export_permission(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["viewer"]})
    resp = client.post("/api/v1/export", json={"question": "导出", "sql": "SELECT region FROM sales"})
    assert resp.status_code == 403


def test_admin_config_requires_manage(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})
    assert client.get("/api/v1/admin/config").status_code == 403


# ===== 多用户隔离测试 =====

def test_admin_user_list_scopes_to_tenant(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "t1", "roles": ["admin"]})
    calls = []
    monkeypatch.setattr(main_module, "list_users", lambda tenant_id: calls.append(tenant_id) or [])

    client.get("/api/v1/admin/users")

    assert calls == ["t1"]


def test_delete_grant_scopes_to_tenant(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "t2", "roles": ["admin"]})
    calls = []
    monkeypatch.setattr(main_module, "delete_user_grant", lambda grant_id, tenant_id: calls.append((grant_id, tenant_id)))

    client.delete("/api/v1/admin/grants/5")

    assert calls == [(5, "t2")]
