"""Runtime repository selection for reflection rollout governance."""

from app.config import settings
from app.audit import write_audit
from app.repositories.memory_reflection_rollout_repository import InMemoryReflectionRolloutRepository
from app.repositories.mysql_reflection_rollout_repository import MySQLReflectionRolloutRepository
from app.services.reflection_rollout_service import ReflectionRolloutService


_memory_repository = InMemoryReflectionRolloutRepository()
_mysql_repository = MySQLReflectionRolloutRepository()


def _audit_rollout(**payload) -> None:
    write_audit(
        settings.audit_log_path,
        trace_id=str(payload.get("version_id", "reflection-rollout")),
        tenant_id=str(payload.get("tenant_id", "default")),
        actor_id=str(payload.get("actor_id", "")),
        sql="",
        duration_ms=0,
        event=str(payload.get("event", "reflection_rollout")),
        operation_name="reflection_rollout",
    )


def get_reflection_rollout_service() -> ReflectionRolloutService:
    repository = _mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository
    return ReflectionRolloutService(repository=repository, audit_writer=_audit_rollout)


def reset_memory_reflection_rollout_repository() -> None:
    _memory_repository.clear()


__all__ = ["get_reflection_rollout_service", "reset_memory_reflection_rollout_repository"]

