from fastapi import Header, HTTPException
from .config import settings
from .jwt_auth import verify_jwt

def require_identity(authorization: str | None = Header(default=None)) -> dict:
    if not settings.auth_enabled:
        return {"subject": "local-dev", "tenant_id": "default", "roles": ["analyst"]}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer 身份凭证")
    token = authorization.removeprefix("Bearer ").strip()
    for key in settings.api_keys.split(","):
        if token == key.strip() and token:
            return {"subject": f"api-key:{token[:6]}", "tenant_id": "default", "roles": ["analyst"]}
    return verify_jwt(authorization)
