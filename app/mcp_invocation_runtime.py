"""Runtime assembly for governed external MCP calls."""

from app.config import settings
from app.mcp_registry_runtime import get_mcp_registry_service
from app.services.mcp_invocation_service import McpInvocationService
from app.services.mcp_runtime_guard import McpRuntimeGuard
from app.services.mcp_transport import OfficialMcpTransport

_transport = OfficialMcpTransport()
_guard = None
_guard_signature = None


def _runtime_guard() -> McpRuntimeGuard:
    global _guard, _guard_signature
    signature = (
        settings.mcp_runtime_backend,
        settings.redis_url,
        settings.mcp_queue_timeout_seconds,
    )
    if _guard is None or signature != _guard_signature:
        _guard = McpRuntimeGuard(
            backend=settings.mcp_runtime_backend,
            redis_url=settings.redis_url,
            queue_timeout_seconds=settings.mcp_queue_timeout_seconds,
        )
        _guard_signature = signature
    return _guard


def get_mcp_invocation_service() -> McpInvocationService:
    return McpInvocationService(
        get_mcp_registry_service(),
        transport=_transport,
        guard=_runtime_guard(),
    )


def reset_memory_mcp_governance() -> None:
    if _guard is not None:
        _guard.reset_memory()

