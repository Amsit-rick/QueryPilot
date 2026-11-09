import pytest
from fastapi import HTTPException
from app.rbac import authorize, require_same_tenant


def test_viewer_can_query_but_not_export():
    identity = {"tenant_id": "t1", "roles": ["viewer"]}
    authorize(identity, "query")
    with pytest.raises(HTTPException) as exc:
        authorize(identity, "export")
    assert exc.value.status_code == 403


def test_tenant_isolation():
    with pytest.raises(HTTPException) as exc:
        require_same_tenant({"tenant_id": "t1"}, "t2")
    assert exc.value.status_code == 403
