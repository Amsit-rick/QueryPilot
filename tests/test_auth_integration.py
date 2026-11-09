"""Optional full auth flow against a real MySQL backend (non-destructive).

Enabled only when ``RUN_MYSQL_TESTS=1`` and demo credentials are provided via
``TEST_USERNAME`` / ``TEST_PASSWORD``. Never changes passwords.
"""
import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not (os.getenv("RUN_MYSQL_TESTS") == "1" and os.getenv("TEST_USERNAME") and os.getenv("TEST_PASSWORD")),
    reason="set RUN_MYSQL_TESTS=1 plus TEST_USERNAME/TEST_PASSWORD to run the full auth flow",
)


@pytest.fixture()
def auth_settings(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", os.getenv("JWT_SECRET", "x" * 32))
    return settings


def test_full_auth_flow(auth_settings):
    from app.main import app
    client = TestClient(app)

    login = client.post("/api/v1/auth/login", json={
        "username": os.environ["TEST_USERNAME"],
        "password": os.environ["TEST_PASSWORD"],
    })
    assert login.status_code == 200, login.json()
    tokens = login.json()

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == os.environ["TEST_USERNAME"]

    refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 200

    logout = client.post("/api/v1/auth/logout", json={"refresh_token": refresh.json()["refresh_token"]})
    assert logout.status_code == 200
