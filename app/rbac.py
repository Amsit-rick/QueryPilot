from fastapi import HTTPException

ROLE_PERMISSIONS = {
    "viewer": {"query"},
    "analyst": {"query", "export"},
    "admin": {"query", "export", "manage"},
}


def authorize(identity: dict, permission: str) -> None:
    permissions = set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in identity.get("roles", [])))
    if permission not in permissions:
        raise HTTPException(status_code=403, detail=f"缺少权限：{permission}")


def require_same_tenant(identity: dict, tenant_id: str) -> None:
    if identity.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=403, detail="禁止访问其他租户数据")
