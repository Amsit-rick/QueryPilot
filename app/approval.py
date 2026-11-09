"""In-process approval store with tenant isolation and expiry.

The store is deliberately small for the local runtime; production deployments
can replace it with a MySQL table without changing the API contract.
"""
from time import time
from uuid import uuid4

_requests: dict[str, dict] = {}


def create(*, identity: dict, question: str, sql: str, params: dict, datasource_id: str, task_id: str = "", ttl_seconds: int = 3600, reasons: list[str] | tuple[str, ...] | None = None, trigger: str = "manual", analysis_mode: str = "none") -> dict:
    approval = {
        "id": str(uuid4()),
        "status": "pending",
        "question": question,
        "sql": sql,
        "params": dict(params),
        "datasource_id": datasource_id,
        "tenant_id": identity.get("tenant_id"),
        "requester_id": identity.get("subject"),
        "requester_roles": list(identity.get("roles") or []),
        "task_id": task_id,
        "reasons": list(reasons or []),
        "trigger": trigger,
        "analysis_mode": analysis_mode,
        "created_at": time(),
        "expires_at": time() + max(60, ttl_seconds),
    }
    _requests[approval["id"]] = approval
    return {k: v for k, v in approval.items() if k not in {"params", "tenant_id", "requester_id", "requester_roles"}}


def list_for(identity: dict) -> list[dict]:
    now = time()
    result = []
    for approval in _requests.values():
        if approval["tenant_id"] != identity.get("tenant_id"):
            continue
        if approval["status"] == "pending" and approval["expires_at"] <= now:
            approval["status"] = "expired"
        result.append({k: v for k, v in approval.items() if k not in {"params", "tenant_id", "requester_id", "requester_roles"}})
    return sorted(result, key=lambda item: item["created_at"], reverse=True)


def get_for(approval_id: str, identity: dict) -> dict | None:
    approval = _requests.get(approval_id)
    if not approval or approval["tenant_id"] != identity.get("tenant_id"):
        return None
    if approval["status"] == "pending" and approval["expires_at"] <= time():
        approval["status"] = "expired"
    return approval
