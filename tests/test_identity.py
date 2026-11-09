import pytest
import jwt
from app.config import settings
from app.identity import hash_password, verify_password, issue_tokens, refresh_token_hash


def test_argon2_password_hash_is_not_reversible():
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_minimum_length():
    with pytest.raises(ValueError):
        hash_password("short")


def test_issue_tokens_and_hash(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "x" * 32)
    tokens = issue_tokens(subject="u1", tenant_id="t1", roles=["analyst"])
    claims = jwt.decode(tokens["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == "u1"
    assert claims["type"] == "access"
    assert refresh_token_hash(tokens["refresh_token"]) != tokens["refresh_token"]
