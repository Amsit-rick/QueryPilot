from __future__ import annotations

import json
import sys
import types

import pytest

from app.agent.checkpoint import load_checkpoint, save_checkpoint
from app.agent.recovery_runtime import build_checkpoint_writer
from app.domain.agent_recovery import (
    CHECKPOINT_SCHEMA_VERSION,
    AgentCheckpoint,
    RecoveryLeaseError,
)
from app.services.agent_recovery_lease_service import (
    AgentRecoveryLeaseService,
    InMemoryAgentRecoveryBackend,
    RedisAgentRecoveryBackend,
)


class Clock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _service(clock: Clock) -> AgentRecoveryLeaseService:
    return AgentRecoveryLeaseService(
        backend=InMemoryAgentRecoveryBackend(now=clock.now),
        lease_seconds=30,
    )


def test_checkpoint_is_versioned_and_strips_runtime_secrets_and_rows():
    checkpoint = AgentCheckpoint.create(
        task_id="task-1",
        tenant_id="tenant-a",
        execution_id="exec-1",
        node_cursor="verify",
        fencing_token=7,
        snapshot={
            "status": "verified",
            "sql": "SELECT region, SUM(amount) FROM sales GROUP BY region",
            "params": {"region": "华东"},
            "provided_params": {"secret": "do-not-store"},
            "result": [{"region": "华东", "amount": 100}],
            "rows": [{"region": "华东", "amount": 100}],
            "param_names": ["region"],
            "param_count": 1,
        },
    )

    payload = checkpoint.to_dict()

    assert payload["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert payload["node_cursor"] == "verify"
    assert payload["snapshot"]["param_names"] == ["region"]
    assert payload["snapshot"]["param_count"] == 1
    assert "params" not in payload["snapshot"]
    assert "provided_params" not in payload["snapshot"]
    assert "result" not in payload["snapshot"]
    assert "rows" not in payload["snapshot"]
    assert "华东" not in str(payload)


def test_future_checkpoint_schema_fails_closed():
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION + 1,
        "task_id": "task-1",
        "tenant_id": "tenant-a",
        "execution_id": "exec-1",
        "node_cursor": "validate",
        "fencing_token": 1,
        "snapshot": {"status": "validated"},
    }

    with pytest.raises(RecoveryLeaseError, match="E_CHECKPOINT_SCHEMA_UNSUPPORTED"):
        AgentCheckpoint.from_dict(payload)


def test_only_one_holder_can_own_a_live_task_lease():
    clock = Clock()
    service = _service(clock)

    first = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")

    with pytest.raises(RecoveryLeaseError, match="E_RECOVERY_LEASE_HELD"):
        service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-b")

    assert first.fencing_token == 1


def test_expired_lease_can_be_taken_over_with_monotonic_fencing_token():
    clock = Clock()
    service = _service(clock)
    first = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")

    clock.advance(31)
    second = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-b")

    assert second.holder_id == "worker-b"
    assert second.fencing_token == first.fencing_token + 1


def test_stale_fencing_token_cannot_commit_recovery_checkpoint():
    clock = Clock()
    service = _service(clock)
    stale = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")
    clock.advance(31)
    current = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-b")
    checkpoint = AgentCheckpoint.create(
        task_id="task-1",
        tenant_id="tenant-a",
        execution_id="exec-2",
        node_cursor="materialize",
        fencing_token=stale.fencing_token,
        snapshot={"status": "completed"},
    )

    with pytest.raises(RecoveryLeaseError, match="E_RECOVERY_FENCING_STALE"):
        service.commit(stale, checkpoint)

    accepted = AgentCheckpoint.create(
        task_id="task-1",
        tenant_id="tenant-a",
        execution_id="exec-2",
        node_cursor="materialize",
        fencing_token=current.fencing_token,
        snapshot={"status": "completed"},
    )
    service.commit(current, accepted)
    assert service.load(task_id="task-1", tenant_id="tenant-a") == accepted


def test_checkpoint_identity_must_match_lease_identity():
    clock = Clock()
    service = _service(clock)
    lease = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")
    checkpoint = AgentCheckpoint.create(
        task_id="task-1",
        tenant_id="tenant-b",
        execution_id="exec-1",
        node_cursor="validate",
        fencing_token=lease.fencing_token,
        snapshot={"status": "validated"},
    )

    with pytest.raises(RecoveryLeaseError, match="E_RECOVERY_IDENTITY_MISMATCH"):
        service.commit(lease, checkpoint)


def test_redis_checkpoint_storage_uses_versioned_envelope(monkeypatch):
    class FakeRedis:
        values = {}

        def setex(self, key, ttl, value):
            self.values[key] = value

        def get(self, key):
            return self.values.get(key)

    fake = FakeRedis()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: fake)),
    )

    assert save_checkpoint("redis://test", "thread-1", {"status": "validated"})
    raw = json.loads(fake.values["query-agent:checkpoint:thread-1"])

    assert raw["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert raw["snapshot"] == {"status": "validated"}
    assert load_checkpoint("redis://test", "thread-1") == {"status": "validated"}


def test_redis_checkpoint_future_version_fails_closed(monkeypatch):
    class FakeRedis:
        def get(self, key):
            return json.dumps({"schema_version": 999, "snapshot": {"status": "completed"}})

    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: FakeRedis())),
    )

    assert load_checkpoint("redis://test", "thread-1") is None


def test_redis_backend_uses_atomic_fencing_contract():
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.checkpoint = None

        def eval(self, script, numkeys, *values):
            self.calls.append((script, numkeys, values))
            if "RECOVERY_ACQUIRE" in script:
                return [1, 3, 1030]
            if "RECOVERY_COMMIT" in script:
                self.checkpoint = values[-1]
                return 1
            raise AssertionError("unexpected lua script")

        def get(self, key):
            return self.checkpoint

    fake = FakeRedis()
    backend = RedisAgentRecoveryBackend(redis_url="redis://test", redis_factory=lambda _: fake, now=lambda: 1000)
    service = AgentRecoveryLeaseService(backend=backend, lease_seconds=30)

    lease = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")
    checkpoint = AgentCheckpoint.create(
        task_id="task-1",
        tenant_id="tenant-a",
        execution_id="exec-1",
        node_cursor="verify",
        fencing_token=lease.fencing_token,
        snapshot={"status": "verified"},
    )
    service.commit(lease, checkpoint)

    assert lease.fencing_token == 3
    assert service.load(task_id="task-1", tenant_id="tenant-a") == checkpoint
    assert any("RECOVERY_ACQUIRE" in call[0] for call in fake.calls)
    assert any("RECOVERY_COMMIT" in call[0] for call in fake.calls)


def test_runtime_writer_persists_node_cursor_with_one_execution_lease():
    clock = Clock()
    service = _service(clock)

    writer = build_checkpoint_writer(
        service=service,
        task_id="task-1",
        tenant_id="tenant-a",
        execution_id="exec-1",
        holder_id="api-1",
    )
    writer({"status": "validated", "params": {"secret": "value"}})
    writer({"status": "verified", "result": [{"secret": "value"}]})

    checkpoint = service.load(task_id="task-1", tenant_id="tenant-a")
    assert checkpoint is not None
    assert checkpoint.execution_id == "exec-1"
    assert checkpoint.node_cursor == "verified"
    assert checkpoint.snapshot == {"status": "verified"}


def test_release_allows_next_holder_and_preserves_monotonic_fencing():
    clock = Clock()
    service = _service(clock)
    first = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")

    service.release(first)
    second = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-b")

    assert second.fencing_token == first.fencing_token + 1


def test_stale_holder_cannot_release_new_holder_lease():
    clock = Clock()
    service = _service(clock)
    stale = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-a")
    clock.advance(31)
    current = service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-b")

    with pytest.raises(RecoveryLeaseError, match="E_RECOVERY_FENCING_STALE"):
        service.release(stale)

    with pytest.raises(RecoveryLeaseError, match="E_RECOVERY_LEASE_HELD"):
        service.acquire(task_id="task-1", tenant_id="tenant-a", holder_id="worker-c")
    service.release(current)
