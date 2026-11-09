"""Runtime wiring for resumable query stream state."""

from __future__ import annotations

import threading

from app.config import settings
from app.repositories.memory_query_stream_repository import InMemoryQueryStreamRepository
from app.repositories.redis_query_stream_repository import RedisQueryStreamRepository
from app.services.durable_query_stream_service import DurableQueryStreamService


_runtime_lock = threading.Lock()
_runtime_signature: tuple | None = None
_runtime_service: DurableQueryStreamService | None = None


def _signature() -> tuple:
    return (
        settings.query_stream_state_backend,
        settings.redis_url if settings.query_stream_state_backend == "redis" else "",
        settings.query_stream_state_max_events,
        settings.query_stream_chunk_rows,
        settings.query_stream_heartbeat_seconds,
        settings.query_stream_poll_seconds,
        settings.query_stream_state_ttl_seconds,
    )


def build_durable_query_stream_service(*, redis_factory=None) -> DurableQueryStreamService:
    backend = str(settings.query_stream_state_backend).lower()
    if backend == "memory":
        repository = InMemoryQueryStreamRepository(
            max_events=settings.query_stream_state_max_events,
        )
    elif backend == "redis":
        repository = RedisQueryStreamRepository(
            redis_url=settings.redis_url,
            max_events=settings.query_stream_state_max_events,
            redis_factory=redis_factory,
        )
    else:
        raise RuntimeError("E_QUERY_STREAM_BACKEND_INVALID:查询流状态后端无效")
    return DurableQueryStreamService(
        repository=repository,
        chunk_rows=settings.query_stream_chunk_rows,
        heartbeat_seconds=settings.query_stream_heartbeat_seconds,
        poll_seconds=settings.query_stream_poll_seconds,
        ttl_seconds=settings.query_stream_state_ttl_seconds,
    )


def get_durable_query_stream_service() -> DurableQueryStreamService:
    global _runtime_service, _runtime_signature

    current_signature = _signature()
    with _runtime_lock:
        if _runtime_service is None or _runtime_signature != current_signature:
            _runtime_service = build_durable_query_stream_service()
            _runtime_signature = current_signature
        return _runtime_service


def reset_memory_query_streams() -> None:
    global _runtime_service, _runtime_signature

    with _runtime_lock:
        if _runtime_service is not None and isinstance(
            _runtime_service.repository,
            InMemoryQueryStreamRepository,
        ):
            _runtime_service.repository.clear()
        _runtime_service = None
        _runtime_signature = None


__all__ = [
    "build_durable_query_stream_service",
    "get_durable_query_stream_service",
    "reset_memory_query_streams",
]
