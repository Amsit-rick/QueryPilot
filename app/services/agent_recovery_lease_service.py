"""Lease and fencing-token governance for concurrent agent recovery."""

from __future__ import annotations

import json
import threading
import time

from app.domain.agent_recovery import AgentCheckpoint, RecoveryLease, RecoveryLeaseError


_REDIS_ACQUIRE_LUA = """
-- RECOVERY_ACQUIRE
local now = tonumber(ARGV[1])
local lease_seconds = tonumber(ARGV[2])
local holder = ARGV[3]
local expires_at = tonumber(redis.call('HGET', KEYS[1], 'expires_at') or '0')
if expires_at > now then
  return {0, tonumber(redis.call('HGET', KEYS[1], 'fencing_token') or '0'), expires_at}
end
local token = tonumber(redis.call('INCR', KEYS[2]))
local next_expiry = now + lease_seconds
redis.call('HSET', KEYS[1], 'holder_id', holder, 'fencing_token', token, 'expires_at', next_expiry)
redis.call('EXPIRE', KEYS[1], math.max(1, lease_seconds + 5))
return {1, token, next_expiry}
"""


_REDIS_COMMIT_LUA = """
-- RECOVERY_COMMIT
local now = tonumber(ARGV[1])
local holder = ARGV[2]
local token = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local lease_seconds = tonumber(ARGV[5])
local payload = ARGV[6]
local current_holder = redis.call('HGET', KEYS[1], 'holder_id')
local current_token = tonumber(redis.call('HGET', KEYS[1], 'fencing_token') or '0')
local expires_at = tonumber(redis.call('HGET', KEYS[1], 'expires_at') or '0')
if current_holder ~= holder or current_token ~= token or expires_at <= now then
  return 0
end
local next_expiry = now + lease_seconds
redis.call('HSET', KEYS[1], 'expires_at', next_expiry)
redis.call('EXPIRE', KEYS[1], math.max(1, lease_seconds + 5))
redis.call('SETEX', KEYS[2], ttl, payload)
return 1
"""


_REDIS_RELEASE_LUA = """
-- RECOVERY_RELEASE
local holder = ARGV[1]
local token = tonumber(ARGV[2])
local current_holder = redis.call('HGET', KEYS[1], 'holder_id')
local current_token = tonumber(redis.call('HGET', KEYS[1], 'fencing_token') or '0')
if current_holder ~= holder or current_token ~= token then
  return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


class InMemoryAgentRecoveryBackend:
    def __init__(self, *, now=None):
        self._now = now or time.time
        self._lock = threading.RLock()
        self._leases: dict[tuple[str, str], RecoveryLease] = {}
        self._tokens: dict[tuple[str, str], int] = {}
        self._checkpoints: dict[tuple[str, str], AgentCheckpoint] = {}

    def acquire(
        self,
        *,
        task_id: str,
        tenant_id: str,
        holder_id: str,
        lease_seconds: float,
    ) -> RecoveryLease:
        key = (tenant_id, task_id)
        with self._lock:
            now = float(self._now())
            current = self._leases.get(key)
            if current is not None and current.expires_at > now:
                raise RecoveryLeaseError("E_RECOVERY_LEASE_HELD", "任务已有有效恢复租约")
            token = self._tokens.get(key, 0) + 1
            lease = RecoveryLease(
                task_id=task_id,
                tenant_id=tenant_id,
                holder_id=holder_id,
                fencing_token=token,
                expires_at=now + lease_seconds,
            )
            self._tokens[key] = token
            self._leases[key] = lease
            return lease

    def commit(
        self,
        lease: RecoveryLease,
        checkpoint: AgentCheckpoint,
        *,
        lease_seconds: float,
    ) -> None:
        key = (lease.tenant_id, lease.task_id)
        with self._lock:
            current = self._leases.get(key)
            now = float(self._now())
            if (
                current is None
                or current.expires_at <= now
                or current.holder_id != lease.holder_id
                or current.fencing_token != lease.fencing_token
            ):
                raise RecoveryLeaseError("E_RECOVERY_FENCING_STALE", "恢复租约已过期或被新持有者接管")
            self._leases[key] = RecoveryLease(
                task_id=current.task_id,
                tenant_id=current.tenant_id,
                holder_id=current.holder_id,
                fencing_token=current.fencing_token,
                expires_at=now + lease_seconds,
            )
            self._checkpoints[key] = checkpoint

    def load(self, *, task_id: str, tenant_id: str) -> AgentCheckpoint | None:
        with self._lock:
            return self._checkpoints.get((tenant_id, task_id))

    def release(self, lease: RecoveryLease) -> None:
        key = (lease.tenant_id, lease.task_id)
        with self._lock:
            current = self._leases.get(key)
            if current is None:
                return
            if (
                current.holder_id != lease.holder_id
                or current.fencing_token != lease.fencing_token
            ):
                raise RecoveryLeaseError("E_RECOVERY_FENCING_STALE", "恢复租约已被新持有者接管")
            self._leases.pop(key, None)


class RedisAgentRecoveryBackend:
    """Redis backend using atomic Lua acquire and fenced checkpoint commit."""

    def __init__(
        self,
        *,
        redis_url: str,
        checkpoint_ttl_seconds: int = 3600,
        redis_factory=None,
        now=None,
    ):
        self.redis_url = redis_url
        self.checkpoint_ttl_seconds = max(1, int(checkpoint_ttl_seconds))
        self._redis_factory = redis_factory or self._default_redis_factory
        self._now = now or time.time

    @staticmethod
    def _default_redis_factory(url: str):
        import redis

        return redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )

    @staticmethod
    def _keys(tenant_id: str, task_id: str) -> tuple[str, str, str]:
        scope = f"{tenant_id}:{task_id}"
        return (
            f"query-agent:recovery:lease:{scope}",
            f"query-agent:recovery:token:{scope}",
            f"query-agent:recovery:checkpoint:{scope}",
        )

    def acquire(
        self,
        *,
        task_id: str,
        tenant_id: str,
        holder_id: str,
        lease_seconds: float,
    ) -> RecoveryLease:
        lease_key, token_key, _ = self._keys(tenant_id, task_id)
        client = self._redis_factory(self.redis_url)
        result = client.eval(
            _REDIS_ACQUIRE_LUA,
            2,
            lease_key,
            token_key,
            int(self._now()),
            max(1, int(lease_seconds)),
            holder_id,
        )
        if not result or int(result[0]) != 1:
            raise RecoveryLeaseError("E_RECOVERY_LEASE_HELD", "任务已有有效恢复租约")
        return RecoveryLease(
            task_id=task_id,
            tenant_id=tenant_id,
            holder_id=holder_id,
            fencing_token=int(result[1]),
            expires_at=float(result[2]),
        )

    def commit(
        self,
        lease: RecoveryLease,
        checkpoint: AgentCheckpoint,
        *,
        lease_seconds: float,
    ) -> None:
        lease_key, _, checkpoint_key = self._keys(lease.tenant_id, lease.task_id)
        payload = json.dumps(checkpoint.to_dict(), ensure_ascii=False, separators=(",", ":"))
        client = self._redis_factory(self.redis_url)
        accepted = client.eval(
            _REDIS_COMMIT_LUA,
            2,
            lease_key,
            checkpoint_key,
            int(self._now()),
            lease.holder_id,
            lease.fencing_token,
            self.checkpoint_ttl_seconds,
            max(1, int(lease_seconds)),
            payload,
        )
        if int(accepted or 0) != 1:
            raise RecoveryLeaseError("E_RECOVERY_FENCING_STALE", "恢复租约已过期或被新持有者接管")

    def load(self, *, task_id: str, tenant_id: str) -> AgentCheckpoint | None:
        _, _, checkpoint_key = self._keys(tenant_id, task_id)
        raw = self._redis_factory(self.redis_url).get(checkpoint_key)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RecoveryLeaseError(
                "E_CHECKPOINT_PAYLOAD_INVALID",
                "checkpoint 载荷不可解析，已拒绝恢复",
            ) from exc
        checkpoint = AgentCheckpoint.from_dict(payload)
        if checkpoint.task_id != task_id or checkpoint.tenant_id != tenant_id:
            raise RecoveryLeaseError("E_RECOVERY_IDENTITY_MISMATCH", "checkpoint 与任务身份不一致")
        return checkpoint

    def release(self, lease: RecoveryLease) -> None:
        lease_key, _, _ = self._keys(lease.tenant_id, lease.task_id)
        released = self._redis_factory(self.redis_url).eval(
            _REDIS_RELEASE_LUA,
            1,
            lease_key,
            lease.holder_id,
            lease.fencing_token,
        )
        if int(released or 0) != 1:
            raise RecoveryLeaseError("E_RECOVERY_FENCING_STALE", "恢复租约已被新持有者接管")


class AgentRecoveryLeaseService:
    def __init__(self, *, backend, lease_seconds: float = 30):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.backend = backend
        self.lease_seconds = float(lease_seconds)

    def acquire(self, *, task_id: str, tenant_id: str, holder_id: str) -> RecoveryLease:
        if not task_id or not tenant_id or not holder_id:
            raise RecoveryLeaseError("E_RECOVERY_IDENTITY_REQUIRED", "恢复租约身份不能为空")
        return self.backend.acquire(
            task_id=task_id,
            tenant_id=tenant_id,
            holder_id=holder_id,
            lease_seconds=self.lease_seconds,
        )

    def commit(self, lease: RecoveryLease, checkpoint: AgentCheckpoint) -> None:
        if (
            checkpoint.task_id != lease.task_id
            or checkpoint.tenant_id != lease.tenant_id
            or checkpoint.fencing_token != lease.fencing_token
        ):
            raise RecoveryLeaseError("E_RECOVERY_IDENTITY_MISMATCH", "checkpoint 与恢复租约身份不一致")
        self.backend.commit(lease, checkpoint, lease_seconds=self.lease_seconds)

    def load(self, *, task_id: str, tenant_id: str) -> AgentCheckpoint | None:
        return self.backend.load(task_id=task_id, tenant_id=tenant_id)

    def release(self, lease: RecoveryLease) -> None:
        self.backend.release(lease)


__all__ = [
    "AgentRecoveryLeaseService",
    "InMemoryAgentRecoveryBackend",
    "RedisAgentRecoveryBackend",
]
