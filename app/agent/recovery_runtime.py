"""Runtime adapter from graph checkpoints to fenced recovery storage."""

from __future__ import annotations

from typing import Any, Callable

from app.domain.agent_recovery import AgentCheckpoint
from app.services.agent_recovery_lease_service import AgentRecoveryLeaseService


def build_checkpoint_writer(
    *,
    service: AgentRecoveryLeaseService,
    task_id: str,
    tenant_id: str,
    execution_id: str,
    holder_id: str,
) -> Callable[[dict[str, Any]], None]:
    lease = service.acquire(task_id=task_id, tenant_id=tenant_id, holder_id=holder_id)

    def write(snapshot: dict[str, Any]) -> None:
        checkpoint = AgentCheckpoint.create(
            task_id=task_id,
            tenant_id=tenant_id,
            execution_id=execution_id,
            node_cursor=str(snapshot.get("status") or "unknown"),
            fencing_token=lease.fencing_token,
            snapshot=snapshot,
        )
        service.commit(lease, checkpoint)

    return write


__all__ = ["build_checkpoint_writer"]
