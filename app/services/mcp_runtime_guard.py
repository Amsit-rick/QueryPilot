"""Fail-closed concurrency, call-budget and circuit governance for external MCP."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4


_RESERVE_LUA = """
local now = tonumber(ARGV[1])
local lease_until = tonumber(ARGV[2])
local token = ARGV[3]
local max_concurrency = tonumber(ARGV[4])
local daily_limit = tonumber(ARGV[5])
local consume_budget = tonumber(ARGV[6])
local open_until = tonumber(redis.call('HGET', KEYS[1], 'open_until') or '0')
if now < open_until then return {0, 1, open_until - now} end
redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, now)
if tonumber(redis.call('ZCARD', KEYS[2])) >= max_concurrency then return {0, 2, 0} end
local used = tonumber(redis.call('GET', KEYS[3]) or '0')
if consume_budget == 1 and used >= daily_limit then return {0, 3, 0} end
redis.call('ZADD', KEYS[2], lease_until, token)
redis.call('PEXPIRE', KEYS[2], math.max(1000, lease_until - now + 5000))
if consume_budget == 1 then
  redis.call('INCR', KEYS[3])
  redis.call('EXPIRE', KEYS[3], 172800)
end
return {1, 0, 0}
"""

_RELEASE_LUA = "redis.call('ZREM', KEYS[1], ARGV[1]); return 1"

_CIRCUIT_LUA = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local reset = tonumber(ARGV[3])
local action = ARGV[4]
if action == 'success' then redis.call('DEL', KEYS[1]); return 1 end
local failures = tonumber(redis.call('HGET', KEYS[1], 'failures') or '0') + 1
local open_until = 0
if failures >= threshold then open_until = now + reset end
redis.call('HSET', KEYS[1], 'failures', failures, 'open_until', open_until)
redis.call('PEXPIRE', KEYS[1], math.max(1000, reset + 5000))
return failures
"""


class McpGuardError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}:{message}")


class McpRuntimeGuard:
    def __init__(
        self,
        *,
        backend: str,
        queue_timeout_seconds: float,
        redis_url: str = "",
        redis_factory=None,
        monotonic=None,
        utc_date=None,
    ):
        self.backend = backend
        self.queue_timeout_seconds = max(0.01, float(queue_timeout_seconds))
        self.redis_url = redis_url
        self._redis_factory = redis_factory or self._default_redis_factory
        self._monotonic = monotonic or time.monotonic
        self._utc_date = utc_date or (lambda: datetime.now(timezone.utc).date().isoformat())
        self._lock = threading.Lock()
        self._active = defaultdict(int)
        self._budgets = defaultdict(int)
        self._circuits = {}

    def _default_redis_factory(self, url: str):
        import redis

        return redis.Redis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )

    @staticmethod
    def _redis_keys(key: str, date_key: str) -> tuple[str, str, str]:
        prefix = f"query-agent:mcp:{key}"
        return f"{prefix}:circuit", f"{prefix}:concurrency", f"{prefix}:budget:{date_key}"

    async def _redis_eval(self, script: str, keys: tuple[str, ...], *args):
        def call():
            client = self._redis_factory(self.redis_url)
            return client.eval(script, len(keys), *keys, *args)

        try:
            return await asyncio.to_thread(call)
        except Exception as exc:
            raise McpGuardError(
                "E_MCP_GOVERNANCE_UNAVAILABLE",
                "共享 MCP 调用治理服务暂时不可用",
            ) from exc

    async def _local_acquire(
        self,
        *,
        key: str,
        max_concurrency: int,
        daily_call_limit: int,
        consume_budget: bool,
    ) -> None:
        date_key = self._utc_date()
        deadline = time.monotonic() + self.queue_timeout_seconds
        while True:
            with self._lock:
                state = self._circuits.get(key, {"failures": 0, "open_until": 0.0})
                if self._monotonic() < state["open_until"]:
                    raise McpGuardError("E_MCP_CIRCUIT_OPEN", "外部 MCP Server 暂时熔断")
                if self._active[key] < max_concurrency:
                    budget_key = (date_key, key)
                    if consume_budget and self._budgets[budget_key] >= daily_call_limit:
                        raise McpGuardError("E_MCP_BUDGET", "外部 MCP Server 当日调用额度已用尽", retryable=False)
                    self._active[key] += 1
                    if consume_budget:
                        self._budgets[budget_key] += 1
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpGuardError("E_MCP_CONCURRENCY", "外部 MCP Server 并发已达上限")
            await asyncio.sleep(min(0.01, remaining))

    def _local_release(self, key: str) -> None:
        with self._lock:
            self._active[key] = max(0, self._active[key] - 1)

    @asynccontextmanager
    async def reserve(
        self,
        *,
        key: str,
        max_concurrency: int,
        daily_call_limit: int,
        consume_budget: bool,
        failure_threshold: int,
        reset_seconds: int,
        lease_seconds: float | None = None,
    ):
        del failure_threshold, reset_seconds
        if self.backend == "memory":
            await self._local_acquire(
                key=key,
                max_concurrency=max_concurrency,
                daily_call_limit=daily_call_limit,
                consume_budget=consume_budget,
            )
            try:
                yield
            finally:
                self._local_release(key)
            return
        if self.backend != "redis" or not self.redis_url:
            raise McpGuardError("E_MCP_GOVERNANCE_UNAVAILABLE", "MCP 调用治理配置不可用")
        token = uuid4().hex
        lease_ms = max(1000, int((lease_seconds or 30.0) * 1000))
        circuit_key, concurrency_key, budget_key = self._redis_keys(key, self._utc_date())
        deadline = time.monotonic() + self.queue_timeout_seconds
        while True:
            now_ms = int(time.time() * 1000)
            result = await self._redis_eval(
                _RESERVE_LUA,
                (circuit_key, concurrency_key, budget_key),
                now_ms,
                now_ms + lease_ms,
                token,
                max(1, int(max_concurrency)),
                max(1, int(daily_call_limit)),
                1 if consume_budget else 0,
            )
            allowed, reason = int(result[0]), int(result[1])
            if allowed:
                break
            if reason == 1:
                raise McpGuardError("E_MCP_CIRCUIT_OPEN", "外部 MCP Server 暂时熔断")
            if reason == 3:
                raise McpGuardError("E_MCP_BUDGET", "外部 MCP Server 当日调用额度已用尽", retryable=False)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpGuardError("E_MCP_CONCURRENCY", "外部 MCP Server 并发已达上限")
            await asyncio.sleep(min(0.01, remaining))
        try:
            yield
        finally:
            await self._redis_eval(_RELEASE_LUA, (concurrency_key,), token)

    async def record_failure(self, key: str, *, failure_threshold: int, reset_seconds: int) -> None:
        if self.backend == "memory":
            with self._lock:
                state = dict(self._circuits.get(key, {"failures": 0, "open_until": 0.0}))
                state["failures"] += 1
                if state["failures"] >= max(1, int(failure_threshold)):
                    state["open_until"] = self._monotonic() + max(1, int(reset_seconds))
                self._circuits[key] = state
            return
        if self.backend != "redis" or not self.redis_url:
            raise McpGuardError("E_MCP_GOVERNANCE_UNAVAILABLE", "MCP 调用治理配置不可用")
        circuit_key = self._redis_keys(key, self._utc_date())[0]
        await self._redis_eval(
            _CIRCUIT_LUA,
            (circuit_key,),
            int(time.time() * 1000),
            max(1, int(failure_threshold)),
            max(1000, int(reset_seconds) * 1000),
            "failure",
        )

    async def record_success(self, key: str) -> None:
        if self.backend == "memory":
            with self._lock:
                self._circuits.pop(key, None)
            return
        if self.backend != "redis" or not self.redis_url:
            raise McpGuardError("E_MCP_GOVERNANCE_UNAVAILABLE", "MCP 调用治理配置不可用")
        circuit_key = self._redis_keys(key, self._utc_date())[0]
        await self._redis_eval(_CIRCUIT_LUA, (circuit_key,), int(time.time() * 1000), 1, 1000, "success")

    def reset_memory(self) -> None:
        with self._lock:
            self._active.clear()
            self._budgets.clear()
            self._circuits.clear()


__all__ = ["McpGuardError", "McpRuntimeGuard"]
