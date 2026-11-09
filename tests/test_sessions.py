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


def test_me_returns_username_role_tenant_and_status(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"], "sid": "s1"})
    monkeypatch.setattr(main_module, "get_user_profile", lambda user_id: {
        "id": 1, "username": "alice", "display_name": "Alice", "role": "admin", "tenant_id": "default",
        "is_active": True, "locked_until": None, "created_at": "2026-09-03T00:00:00",
    })

    body = client.get("/api/v1/auth/me").json()

    assert body["username"] == "alice"
    assert body["display_name"] == "Alice"
    assert body["role"] == "admin"
    assert body["tenant_id"] == "default"
    assert body["is_active"] is True
    assert body["sid"] == "s1"


def test_me_keeps_non_account_identity_unchanged(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})

    assert client.get("/api/v1/auth/me").json()["subject"] == "local-dev"


def test_sessions_list_marks_current_device(client, monkeypatch):
    _override({"subject": "1", "tenant_id": "default", "roles": ["admin"], "sid": "s1"})
    monkeypatch.setattr(main_module, "list_sessions", lambda user_id: [
        {"id": "s1", "created_at": "2026-09-03T10:00:00", "last_active_at": "2026-09-03T10:05:00", "expires_at": None, "ip_address": "1.2.3.4", "user_agent": "Chrome"},
        {"id": "s2", "created_at": "2026-09-03T09:00:00", "last_active_at": "2026-09-03T09:30:00", "expires_at": None, "ip_address": "5.6.7.8", "user_agent": "Firefox"},
    ])

    body = client.get("/api/v1/auth/sessions").json()

    assert body[0]["id"] == "s1" and body[0]["current"] is True
    assert body[1]["id"] == "s2" and body[1]["current"] is False


def test_sessions_list_returns_empty_for_non_account_identity(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})

    assert client.get("/api/v1/auth/sessions").json() == []


def test_revoke_single_session_scopes_to_user(client, monkeypatch):
    _override({"subject": "7", "tenant_id": "default", "roles": ["admin"]})
    calls = []
    monkeypatch.setattr(main_module, "revoke_session_by_id", lambda user_id, sid: calls.append((user_id, sid)))

    resp = client.post("/api/v1/auth/sessions/xyz/revoke")

    assert resp.status_code == 200
    assert calls == [(7, "xyz")]


def test_revoke_all_sessions_revokes_current_user_sessions(client, monkeypatch):
    _override({"subject": "7", "tenant_id": "default", "roles": ["admin"]})
    calls = []
    monkeypatch.setattr(main_module, "revoke_user_sessions", lambda user_id: calls.append(user_id))

    resp = client.post("/api/v1/auth/sessions/revoke-all")

    assert resp.status_code == 200
    assert calls == [7]


def test_session_management_rejects_non_numeric_subject(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})

    assert client.post("/api/v1/auth/sessions/xyz/revoke").status_code == 400
    assert client.post("/api/v1/auth/sessions/revoke-all").status_code == 400


def test_profile_update_persists_display_name(client, monkeypatch):
    _override({"subject": "7", "tenant_id": "default", "roles": ["analyst"]})
    calls = []
    monkeypatch.setattr(main_module, "update_profile", lambda user_id, value: calls.append((user_id, value)))
    response = client.patch("/api/v1/auth/profile", json={"display_name": "  数据团队  "})
    assert response.status_code == 200
    assert response.json()["display_name"] == "数据团队"
    assert calls == [(7, "数据团队")]


def test_profile_update_rejects_non_account_identity(client):
    _override({"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]})
    assert client.patch("/api/v1/auth/profile", json={"display_name": "demo"}).status_code == 400
