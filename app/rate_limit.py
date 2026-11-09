"""Per-user rate limiting with an optional Redis shared counter.

The in-process window remains the development default. Redis mode uses one
atomic Lua script so limits are shared by all API instances; Redis failures
fail closed instead of silently falling back to a per-process quota.
"""

import time
from collections import defaultdict, deque
from uuid import uuid4

from fastapi import HTTPException

_requests = defaultdict(deque)

_SLIDING_WINDOW_LUA = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then retry = math.max(1, window - (now - tonumber(oldest[2]))) end
  return {0, retry}
end
redis.call('ZADD', KEYS[1], now, member)
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""


def _reject(retry_after: int) -> None:
    raise HTTPException(
        status_code=429,
        detail="请求频率超过限制",
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def _enforce_local(key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket = _requests[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        _reject(window_seconds - (now - bucket[0]))
    bucket.append(now)


def _enforce_redis(key: str, limit: int, window_seconds: int, redis_url: str) -> None:
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        now_ms = int(time.time() * 1000)
        result = client.eval(
            _SLIDING_WINDOW_LUA,
            1,
            f"query-agent:rate-limit:{key}",
            now_ms,
            int(window_seconds * 1000),
            int(limit),
            f"{now_ms}-{uuid4().hex}",
        )
        if int(result[0]) == 0:
            _reject(max(1, int(result[1]) // 1000))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="共享限流服务暂时不可用") from exc


def enforce_rate_limit(
    key: str,
    limit: int = 60,
    window_seconds: int = 60,
    *,
    redis_url: str | None = None,
) -> None:
    """Consume one quota slot, using Redis when ``redis_url`` is supplied."""
    if limit <= 0 or window_seconds <= 0:
        raise ValueError("rate-limit limit and window must be positive")
    if redis_url:
        _enforce_redis(str(key), limit, window_seconds, redis_url)
    else:
        _enforce_local(str(key), limit, window_seconds)
