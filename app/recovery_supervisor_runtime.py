"""Runtime wiring for explicit, one-shot recovery supervisor execution."""

from app.config import settings
from app.query_admission_runtime import get_query_admission_service
from app.services.agent_recovery_lease_service import (
    AgentRecoveryLeaseService,
    RedisAgentRecoveryBackend,
)
from app.services.recovery_supervisor_service import RecoverySupervisorService
from app.services.task_recovery_service import TaskRecoveryService
from app.task_runtime import get_task_service
from app.tool_replay_runtime import get_tool_replay_policy_service


def get_recovery_supervisor_service() -> RecoverySupervisorService:
    if not settings.agent_checkpoint_enabled:
        raise RuntimeError(
            "E_RECOVERY_SUPERVISOR_DISABLED:请先显式启用 AGENT_CHECKPOINT_ENABLED"
        )
    if not settings.redis_url.strip():
        raise RuntimeError("E_RECOVERY_REDIS_REQUIRED:恢复监督器需要 Redis")
    tasks = get_task_service()
    leases = AgentRecoveryLeaseService(
        backend=RedisAgentRecoveryBackend(
            redis_url=settings.redis_url,
            checkpoint_ttl_seconds=settings.agent_checkpoint_ttl_seconds,
        ),
        lease_seconds=settings.agent_recovery_lease_seconds,
    )
    return RecoverySupervisorService(
        tasks=tasks,
        admissions=get_query_admission_service(),
        leases=leases,
        task_recovery=TaskRecoveryService(tasks),
        replay_policies=get_tool_replay_policy_service(),
    )


__all__ = ["get_recovery_supervisor_service"]
