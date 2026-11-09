"""Key-scoped circuit-breaker state for external providers."""

from __future__ import annotations

import time

_memory_states: dict[str, tuple[int, float]] = {}

_LUA = """
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local reset = tonumber(ARGV[3])
local action = ARGV[4]
local failures = tonumber(redis.call('HGET', KEYS[1], 'failures') or '0')
local open_until = tonumber(redis.call('HGET', KEYS[1], 'open_until') or '0')
if action == 'allow' then
  if now < open_until then return {0, open_until - now} end
  return {1, 0}
end
if action == 'success' then
  redis.call('DEL', KEYS[1])
  return {1, 0}
end
failures = failures + 1
if failures >= threshold then
  open_until = now + reset
  redis.call('HSET', KEYS[1], 'failures', failures, 'open_until', open_until)
  redis.call('EXPIRE', KEYS[1], math.max(1, reset))
  return {0, reset}
end
redis.call('HSET', KEYS[1], 'failures', failures, 'open_until', 0)
redis.call('EXPIRE', KEYS[1], math.max(1, reset))
return {1, 0}
"""


class CircuitBackendUnavailable(RuntimeError):
    """The shared circuit state cannot be read or updated safely."""


def _redis_call(redis_url: str, key: str, action: str, threshold: int, reset_seconds: int) -> tuple[int, int]:
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        result = client.eval(
            _LUA,
            1,
            f"query-agent:circuit:{key}",
            int(time.time()),
            max(1, int(threshold)),
            max(1, int(reset_seconds)),
            action,
        )
        return int(result[0]), int(result[1])
    except Exception as exc:
        raise CircuitBackendUnavailable("共享模型熔断服务暂时不可用") from exc


def allow_request(*, key: str, threshold: int, reset_seconds: int, redis_url: str | None = None) -> None:
    """Raise when the circuit is open or shared state cannot be read."""
    if redis_url:
        allowed, retry = _redis_call(redis_url, key, "allow", threshold, reset_seconds)
        if not allowed:
            raise RuntimeError(f"LLM 服务暂时熔断，请稍后重试（约 {max(1, retry)} 秒）")
        return
    _failures, open_until = _memory_states.get(key, (0, 0.0))
    if time.monotonic() < open_until:
        raise RuntimeError("LLM 服务暂时熔断，请稍后重试")


def record_failure(*, key: str, threshold: int, reset_seconds: int, redis_url: str | None = None) -> None:
    if redis_url:
        _redis_call(redis_url, key, "failure", threshold, reset_seconds)
        return
    failures, _open_until = _memory_states.get(key, (0, 0.0))
    failures += 1
    open_until = time.monotonic() + reset_seconds if failures >= threshold else 0.0
    _memory_states[key] = (failures, open_until)


def record_success(*, key: str, threshold: int, reset_seconds: int, redis_url: str | None = None) -> None:
    if redis_url:
        _redis_call(redis_url, key, "success", threshold, reset_seconds)
        return
    _memory_states.pop(key, None)


def reset_memory() -> None:
    _memory_states.clear()


__all__ = ["CircuitBackendUnavailable", "allow_request", "record_failure", "record_success", "reset_memory"]
