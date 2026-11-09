"""Reusable HTTP dependencies for authentication and authorization."""
from fastapi import Depends

from app.auth import require_identity
from app.rbac import authorize


def require_query_access(identity: dict = Depends(require_identity)) -> dict:
    authorize(identity, "query")
    return identity


def require_export_access(identity: dict = Depends(require_identity)) -> dict:
    authorize(identity, "export")
    return identity


def require_manage_access(identity: dict = Depends(require_identity)) -> dict:
    authorize(identity, "manage")
    return identity
