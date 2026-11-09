"""Redis-backed bounded query stream state for multi-worker deployments."""

from __future__ import annotations

from hashlib import sha256
import json
import time
from uuid import uuid4

from app.domain.query_stream import (
    QueryStreamEvent,
    QueryStreamScope,
    QueryStreamSession,
    apply_stream_event,
    initial_stream_snapshot,
    sanitize_stream_data,
)


_CLAIM_SCRIPT = r"""
-- QUERY_STREAM_CLAIM
local existing = redis.call('GET', KEYS[1])
if existing then
  local existing_key = KEYS[2] .. existing
  local raw = redis.call('GET', existing_key)
  if raw then
    local state = cjson.decode(raw)
    state['expires_at'] = tonumber(ARGV[4]) + tonumber(ARGV[2])
    raw = cjson.encode(state)
    redis.call('SET', existing_key, raw, 'EX', ARGV[2])
    redis.call('EXPIRE', KEYS[1], ARGV[2])
    if redis.call('EXISTS', KEYS[3] .. existing) == 1 then
      redis.call('EXPIRE', KEYS[3] .. existing, ARGV[2])
    end
    return {existing, 0, raw}
  end
  redis.call('DEL', KEYS[1])
end

local claimed = redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2], 'NX')
if claimed then
  redis.call('SET', KEYS[2] .. ARGV[1], ARGV[3], 'EX', ARGV[2])
  return {ARGV[1], 1, ARGV[3]}
end

existing = redis.call('GET', KEYS[1])
if not existing then
  return redis.error_reply('QUERY_STREAM_CLAIM_RACE')
end
local raw = redis.call('GET', KEYS[2] .. existing)
if not raw then
  return redis.error_reply('QUERY_STREAM_SESSION_MISSING')
end
return {existing, 0, raw}
"""


_APPEND_SCRIPT = r"""
-- QUERY_STREAM_APPEND
local raw = redis.call('GET', KEYS[1])
if not raw then
  return {-1, ''}
end
local current = cjson.decode(raw)
if current['scope_fingerprint'] ~= ARGV[1] then
  return {-2, ''}
end
if tonumber(current['last_sequence']) ~= tonumber(ARGV[2]) then
  return {-3, ''}
end

redis.call('RPUSH', KEYS[2], ARGV[4])
redis.call('LTRIM', KEYS[2], -tonumber(ARGV[6]), -1)
redis.call('SET', KEYS[1], ARGV[3], 'EX', ARGV[5])
redis.call('EXPIRE', KEYS[2], ARGV[5])
redis.call('EXPIRE', KEYS[3], ARGV[5])
return {tonumber(ARGV[2]) + 1, ARGV[4]}
"""


class RedisQueryStreamRepository:
    def __init__(
        self,
        *,
        redis_url: str,
        max_events: int = 2048,
        namespace: str = "query-pilot:query-stream:v2",
        redis_factory=None,
        now=None,
    ):
        if not str(redis_url).strip():
            raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:未配置 Redis URL")
        self.redis_url = str(redis_url)
        self.max_events = max(1, int(max_events))
        self.namespace = str(namespace).strip(":")
        self._now = now or time.time
        self._redis_factory = redis_factory or self._default_redis_factory
        try:
            self._client = self._redis_factory(self.redis_url)
            self._client.ping()
        except Exception as exc:
            raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:查询流 Redis 不可用") from exc

    @staticmethod
    def _default_redis_factory(redis_url: str):
        import redis

        return redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )

    @staticmethod
    def _scope_fingerprint(scope: QueryStreamScope) -> str:
        raw = "\x1f".join(scope.key).encode("utf-8")
        return sha256(raw).hexdigest()

    def _scope_key(self, fingerprint: str) -> str:
        return f"{self.namespace}:scope:{fingerprint}"

    @property
    def _session_prefix(self) -> str:
        return f"{self.namespace}:session:"

    def _session_key(self, stream_id: str) -> str:
        return f"{self._session_prefix}{stream_id}"

    def _events_key(self, stream_id: str) -> str:
        return f"{self.namespace}:events:{stream_id}"

    @staticmethod
    def _encode(value: dict) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _decode(raw) -> dict:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(str(raw))

    def _session_payload(
        self,
        *,
        stream_id: str,
        scope_fingerprint: str,
        snapshot: dict,
        last_sequence: int,
        first_sequence: int,
        terminal: bool,
        expires_at: float,
    ) -> dict:
        return {
            "id": str(stream_id),
            "scope_fingerprint": str(scope_fingerprint),
            "snapshot": sanitize_stream_data(snapshot),
            "last_sequence": int(last_sequence),
            "first_sequence": int(first_sequence),
            "terminal": bool(terminal),
            "expires_at": float(expires_at),
        }

    def _to_session(
        self,
        payload: dict,
        *,
        scope: QueryStreamScope,
        expected_fingerprint: str,
    ) -> QueryStreamSession | None:
        if payload.get("scope_fingerprint") != expected_fingerprint:
            return None
        return QueryStreamSession(
            id=str(payload["id"]),
            scope=scope,
            snapshot=sanitize_stream_data(payload.get("snapshot") or initial_stream_snapshot()),
            last_sequence=int(payload.get("last_sequence", 0)),
            first_sequence=int(payload.get("first_sequence", 1)),
            terminal=bool(payload.get("terminal", False)),
            expires_at=float(payload.get("expires_at", 0)),
        )

    def claim(self, *, scope: QueryStreamScope, ttl_seconds: int) -> tuple[QueryStreamSession, bool]:
        ttl = max(1, int(ttl_seconds))
        fingerprint = self._scope_fingerprint(scope)
        stream_id = str(uuid4())
        now = float(self._now())
        payload = self._session_payload(
            stream_id=stream_id,
            scope_fingerprint=fingerprint,
            snapshot=initial_stream_snapshot(),
            last_sequence=0,
            first_sequence=1,
            terminal=False,
            expires_at=now + ttl,
        )
        try:
            result = self._client.eval(
                _CLAIM_SCRIPT,
                3,
                self._scope_key(fingerprint),
                self._session_prefix,
                f"{self.namespace}:events:",
                stream_id,
                ttl,
                self._encode(payload),
                now,
            )
            stored_id, created, raw = result
            decoded = self._decode(raw)
        except Exception as exc:
            raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:查询流认领失败") from exc
        session = self._to_session(
            decoded,
            scope=scope,
            expected_fingerprint=fingerprint,
        )
        if session is None or session.id != str(stored_id):
            raise RuntimeError("E_QUERY_STREAM_REDIS_CORRUPT:查询流状态身份不一致")
        return session, bool(int(created))

    def get(self, *, stream_id: str, scope: QueryStreamScope) -> QueryStreamSession | None:
        fingerprint = self._scope_fingerprint(scope)
        try:
            raw = self._client.get(self._session_key(stream_id))
        except Exception as exc:
            raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:查询流读取失败") from exc
        if raw is None:
            return None
        session = self._to_session(
            self._decode(raw),
            scope=scope,
            expected_fingerprint=fingerprint,
        )
        if session is None or session.id != str(stream_id):
            return None
        return session

    def append(
        self,
        *,
        stream_id: str,
        scope: QueryStreamScope,
        event: str,
        data: dict,
        ttl_seconds: int,
    ) -> QueryStreamEvent:
        ttl = max(1, int(ttl_seconds))
        fingerprint = self._scope_fingerprint(scope)
        safe_data = sanitize_stream_data(data)
        for _ in range(8):
            current = self.get(stream_id=stream_id, scope=scope)
            if current is None:
                raise LookupError("E_QUERY_STREAM_NOT_FOUND:查询流不存在")
            sequence = current.last_sequence + 1
            event_value = QueryStreamEvent(
                stream_id=str(stream_id),
                sequence=sequence,
                event=str(event),
                data=safe_data,
            )
            first_sequence = max(1, sequence - self.max_events + 1)
            snapshot = apply_stream_event(
                current.snapshot,
                sequence=sequence,
                event=event_value.event,
                data=event_value.data,
            )
            session_payload = self._session_payload(
                stream_id=stream_id,
                scope_fingerprint=fingerprint,
                snapshot=snapshot,
                last_sequence=sequence,
                first_sequence=first_sequence,
                terminal=bool(snapshot.get("terminal")),
                expires_at=float(self._now()) + ttl,
            )
            event_payload = {
                "stream_id": event_value.stream_id,
                "sequence": event_value.sequence,
                "event": event_value.event,
                "data": event_value.data,
            }
            try:
                result = self._client.eval(
                    _APPEND_SCRIPT,
                    3,
                    self._session_key(stream_id),
                    self._events_key(stream_id),
                    self._scope_key(fingerprint),
                    fingerprint,
                    current.last_sequence,
                    self._encode(session_payload),
                    self._encode(event_payload),
                    ttl,
                    self.max_events,
                )
            except Exception as exc:
                raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:查询流事件提交失败") from exc
            status = int(result[0])
            if status == -3:
                continue
            if status in {-1, -2}:
                raise LookupError("E_QUERY_STREAM_NOT_FOUND:查询流不存在")
            if status != sequence:
                raise RuntimeError("E_QUERY_STREAM_REDIS_CORRUPT:查询流序号异常")
            return event_value
        raise RuntimeError("E_QUERY_STREAM_CONFLICT:查询流并发提交冲突")

    def list_after(
        self,
        *,
        stream_id: str,
        scope: QueryStreamScope,
        after_sequence: int,
        limit: int = 256,
    ) -> list[QueryStreamEvent]:
        if self.get(stream_id=stream_id, scope=scope) is None:
            raise LookupError("E_QUERY_STREAM_NOT_FOUND:查询流不存在")
        try:
            rows = self._client.lrange(self._events_key(stream_id), 0, -1)
        except Exception as exc:
            raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:查询流事件读取失败") from exc
        values: list[QueryStreamEvent] = []
        for raw in rows:
            payload = self._decode(raw)
            sequence = int(payload["sequence"])
            if sequence <= int(after_sequence):
                continue
            values.append(
                QueryStreamEvent(
                    stream_id=str(payload["stream_id"]),
                    sequence=sequence,
                    event=str(payload["event"]),
                    data=sanitize_stream_data(payload.get("data") or {}),
                )
            )
        return values[: max(1, min(int(limit), self.max_events))]

    def clear(self) -> None:
        try:
            keys = list(self._client.scan_iter(match=f"{self.namespace}:*"))
            if keys:
                self._client.delete(*keys)
        except Exception as exc:
            raise RuntimeError("E_QUERY_STREAM_REDIS_UNAVAILABLE:查询流清理失败") from exc


__all__ = ["RedisQueryStreamRepository"]
