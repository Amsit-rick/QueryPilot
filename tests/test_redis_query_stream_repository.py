from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.domain.query_stream import QueryStreamScope, apply_stream_event, initial_stream_snapshot
from app.repositories.redis_query_stream_repository import RedisQueryStreamRepository


class FakeRedis:
    def __init__(self, *, broken: bool = False):
        self.broken = broken
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.keys_seen: list[str] = []

    def ping(self):
        if self.broken:
            raise ConnectionError("redis unavailable")
        return True

    def eval(self, script, numkeys, *values):
        keys = list(values[:numkeys])
        args = list(values[numkeys:])
        self.keys_seen.extend(str(key) for key in keys)
        if "QUERY_STREAM_CLAIM" in script:
            scope_key, session_prefix, events_prefix = keys
            stream_id, ttl, session_json, now = args
            existing_id = self.values.get(scope_key)
            if existing_id and f"{session_prefix}{existing_id}" in self.values:
                return [existing_id, 0, self.values[f"{session_prefix}{existing_id}"]]
            self.values[scope_key] = stream_id
            self.values[f"{session_prefix}{stream_id}"] = session_json
            return [stream_id, 1, session_json]
        if "QUERY_STREAM_APPEND" in script:
            session_key, events_key, scope_key = keys
            scope_fingerprint, expected_sequence, session_json, event_json, ttl, max_events = args
            session = json.loads(self.values[session_key])
            if session["scope_fingerprint"] != scope_fingerprint:
                return [-2, ""]
            if int(session["last_sequence"]) != int(expected_sequence):
                return [-3, ""]
            event = json.loads(event_json)
            sequence = int(event["sequence"])
            rows = self.lists.setdefault(events_key, [])
            rows.append(event_json)
            del rows[: max(0, len(rows) - int(max_events))]
            self.values[session_key] = session_json
            return [sequence, event_json]
        raise AssertionError("unexpected Lua script")

    def get(self, key):
        self.keys_seen.append(str(key))
        return self.values.get(key)

    def lrange(self, key, start, end):
        self.keys_seen.append(str(key))
        values = self.lists.get(key, [])
        return deepcopy(values[start:] if end == -1 else values[start : end + 1])

    def scan_iter(self, match):
        prefix = str(match).removesuffix("*")
        return [key for key in [*self.values, *self.lists] if key.startswith(prefix)]

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.lists.pop(key, None)


def _scope(*, tenant: str = "tenant-a", owner: str = "user-a") -> QueryStreamScope:
    return QueryStreamScope(
        tenant_id=tenant,
        owner_id=owner,
        operation="query",
        client_request_id="request-sensitive-123",
    )


def test_redis_repository_claims_once_and_persists_monotonic_bounded_events():
    fake = FakeRedis()
    repository = RedisQueryStreamRepository(
        redis_url="redis://test",
        max_events=2,
        redis_factory=lambda _: fake,
    )

    first, created = repository.claim(scope=_scope(), ttl_seconds=60)
    duplicate, duplicate_created = repository.claim(scope=_scope(), ttl_seconds=60)
    repository.append(
        stream_id=first.id,
        scope=_scope(),
        event="started",
        data={"stream_id": first.id},
        ttl_seconds=60,
    )
    repository.append(
        stream_id=first.id,
        scope=_scope(),
        event="result",
        data={"task_id": "task-1", "rows": []},
        ttl_seconds=60,
    )
    repository.append(
        stream_id=first.id,
        scope=_scope(),
        event="completed",
        data={"row_count": 0},
        ttl_seconds=60,
    )

    current = repository.get(stream_id=first.id, scope=_scope())
    events = repository.list_after(
        stream_id=first.id,
        scope=_scope(),
        after_sequence=0,
        limit=10,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert current is not None
    assert current.last_sequence == 3
    assert current.first_sequence == 2
    assert current.terminal is True
    assert current.snapshot["status"] == "completed"
    assert [event.sequence for event in events] == [2, 3]


def test_redis_repository_hashes_scope_keys_and_enforces_scope_identity():
    fake = FakeRedis()
    repository = RedisQueryStreamRepository(
        redis_url="redis://test",
        redis_factory=lambda _: fake,
    )
    session, _ = repository.claim(scope=_scope(), ttl_seconds=60)

    assert repository.get(stream_id=session.id, scope=_scope(tenant="tenant-b")) is None
    joined_keys = " ".join(fake.keys_seen)
    assert "tenant-a" not in joined_keys
    assert "user-a" not in joined_keys
    assert "request-sensitive-123" not in joined_keys


def test_redis_repository_fails_closed_when_redis_is_unavailable():
    with pytest.raises(RuntimeError, match="E_QUERY_STREAM_REDIS_UNAVAILABLE"):
        RedisQueryStreamRepository(
            redis_url="redis://test",
            redis_factory=lambda _: FakeRedis(broken=True),
        )


def test_initial_snapshot_contract_remains_serializable():
    assert json.loads(json.dumps(initial_stream_snapshot()))["last_sequence"] == 0


def test_runtime_builder_selects_redis_backend(monkeypatch):
    from app import query_stream_runtime as runtime

    fake = FakeRedis()
    monkeypatch.setattr(runtime.settings, "query_stream_state_backend", "redis")
    monkeypatch.setattr(runtime.settings, "redis_url", "redis://test")

    service = runtime.build_durable_query_stream_service(
        redis_factory=lambda _: fake,
    )

    assert isinstance(service.repository, RedisQueryStreamRepository)
