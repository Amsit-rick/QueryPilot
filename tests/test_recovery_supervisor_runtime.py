from __future__ import annotations

import pytest

from app import recovery_supervisor_runtime as runtime
from app.services.recovery_supervisor_service import RecoverySupervisorService


def test_runtime_is_disabled_unless_checkpoint_recovery_is_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(runtime.settings, "agent_checkpoint_enabled", False)

    with pytest.raises(RuntimeError, match="E_RECOVERY_SUPERVISOR_DISABLED"):
        runtime.get_recovery_supervisor_service()


def test_runtime_requires_redis_and_wires_existing_services(monkeypatch):
    monkeypatch.setattr(runtime.settings, "agent_checkpoint_enabled", True)
    monkeypatch.setattr(runtime.settings, "redis_url", "")
    with pytest.raises(RuntimeError, match="E_RECOVERY_REDIS_REQUIRED"):
        runtime.get_recovery_supervisor_service()

    backend = object()
    monkeypatch.setattr(runtime.settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(runtime, "RedisAgentRecoveryBackend", lambda **_kwargs: backend)
    service = runtime.get_recovery_supervisor_service()

    assert isinstance(service, RecoverySupervisorService)
    assert service.leases.backend is backend

