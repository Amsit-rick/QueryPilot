"""Production identity primitives: Argon2id passwords and short-lived JWTs."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .config import settings

PASSWORD_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符")
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def issue_tokens(*, subject: str, tenant_id: str, roles: list[str], session_id: str | None = None) -> dict[str, str | int]:
    now = datetime.now(timezone.utc)
    access_exp = now + timedelta(minutes=15)
    refresh_exp = now + timedelta(days=14)
    sid = session_id or str(uuid.uuid4())
    base = {"sub": subject, "tenant_id": tenant_id, "roles": roles, "sid": sid}
    access = jwt.encode({**base, "type": "access", "iat": now, "exp": access_exp}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    refresh = jwt.encode({**base, "type": "refresh", "iat": now, "exp": refresh_exp, "nonce": secrets.token_urlsafe(16)}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer", "expires_in": int((access_exp - now).total_seconds()), "session_id": sid}


def refresh_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_refresh_claims(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm], options={"verify_exp": True, "verify_aud": False, "verify_iss": False})
