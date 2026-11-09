import time
import jwt
import pytest
from fastapi import HTTPException
from app.jwt_auth import verify_jwt
from app.auth import require_identity

def test_jwt_claims_are_verified(monkeypatch):
    from app.config import settings
    secret = "test-secret-with-at-least-32-bytes"
    monkeypatch.setattr(settings, "auth_enabled", True); monkeypatch.setattr(settings, "jwt_secret", secret)
    token = jwt.encode({"sub":"u1","tenant_id":"t1","roles":["analyst"],"exp":int(time.time())+60}, secret, algorithm="HS256")
    assert verify_jwt(f"Bearer {token}")["tenant_id"] == "t1"

def test_invalid_jwt_rejected(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "auth_enabled", True); monkeypatch.setattr(settings, "jwt_secret", "test-secret-with-at-least-32-bytes")
    with pytest.raises(HTTPException) as exc: verify_jwt("Bearer invalid")
    assert exc.value.status_code == 401

def test_query_identity_accepts_login_jwt(monkeypatch):
    from app.config import settings
    secret = "test-secret-with-at-least-32-bytes"
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", secret)
    monkeypatch.setattr(settings, "api_keys", "")
    token = jwt.encode({"sub":"1","tenant_id":"default","roles":["admin"],"type":"access","exp":int(time.time())+60}, secret, algorithm="HS256")
    identity = require_identity(f"Bearer {token}")
    assert identity == {"subject":"1","tenant_id":"default","roles":["admin"]}
