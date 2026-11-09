import jwt
from fastapi import Header, HTTPException
from .config import settings

def verify_jwt(authorization: str | None = Header(default=None)) -> dict:
    if not settings.auth_enabled:
        return {"subject": "local-dev", "tenant_id": "default", "roles": ["admin"]}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Bearer JWT")
    token = authorization[7:].strip()
    secret = settings.jwt_secret
    if not secret:
        raise HTTPException(status_code=503, detail="JWT 验证未配置密钥或 JWKS")
    keys = [secret] + [k.strip() for k in settings.jwt_previous_secrets.split(",") if k.strip()]
    claims = None
    for key in keys:
        try:
            claims = jwt.decode(token, key, algorithms=[settings.jwt_algorithm], audience=settings.jwt_audience or None, issuer=settings.jwt_issuer or None, options={"verify_aud": bool(settings.jwt_audience), "verify_iss": bool(settings.jwt_issuer)})
            break
        except jwt.PyJWTError:
            continue
    if claims is None:
        raise HTTPException(status_code=401, detail="JWT 无效或已过期")
    subject = claims.get("sub")
    tenant_id = claims.get("tenant_id") or claims.get("tid")
    roles = claims.get("roles", [])
    if not subject or not tenant_id or not isinstance(roles, list):
        raise HTTPException(status_code=401, detail="JWT 缺少 sub、tenant_id 或 roles")
    identity = {"subject": subject, "tenant_id": tenant_id, "roles": roles}
    if claims.get("sid"):
        identity["sid"] = claims["sid"]
    return identity
