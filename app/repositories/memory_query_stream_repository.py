"""Thread-safe bounded query stream state for local and single-worker use."""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from uuid import uuid4

from app.domain.query_stream import (
    QueryStreamEvent,
    QueryStreamScope,
    QueryStreamSession,
    apply_stream_event,
)


class InMemoryQueryStreamRepository:
    def __init__(self, *, max_events: int = 2048, now=None):
        self.max_events = max(1, int(max_events))
        self._now = now or time.time
        self._lock = threading.RLock()
        self._scope_index: dict[tuple[str, str, str, str], str] = {}
        self._sessions: dict[str, QueryStreamSession] = {}
        self._events: dict[str, list[QueryStreamEvent]] = {}

    @staticmethod
    def _copy_session(session: QueryStreamSession) -> QueryStreamSession:
        return QueryStreamSession(
            id=session.id,
            scope=session.scope,
            snapshot=deepcopy(session.snapshot),
            last_sequence=session.last_sequence,
            first_sequence=session.first_sequence,
            terminal=session.terminal,
            expires_at=session.expires_at,
        )

    def claim(self, *, scope: QueryStreamScope, ttl_seconds: int) -> tuple[QueryStreamSession, bool]:
        with self._lock:
            now = float(self._now())
            existing_id = self._scope_index.get(scope.key)
            existing = self._sessions.get(existing_id or "")
            if existing is not None and existing.expires_at > now:
                existing.expires_at = now + max(1, int(ttl_seconds))
                return self._copy_session(existing), False
            if existing_id:
                self._sessions.pop(existing_id, None)
                self._events.pop(existing_id, None)
            session = QueryStreamSession(
                id=str(uuid4()),
                scope=scope,
                expires_at=now + max(1, int(ttl_seconds)),
            )
            self._scope_index[scope.key] = session.id
            self._sessions[session.id] = session
            self._events[session.id] = []
            return self._copy_session(session), True

    def get(self, *, stream_id: str, scope: QueryStreamScope) -> QueryStreamSession | None:
        with self._lock:
            session = self._sessions.get(stream_id)
            if session is None or session.scope != scope or session.expires_at <= float(self._now()):
                return None
            return self._copy_session(session)

    def append(
        self,
        *,
        stream_id: str,
        scope: QueryStreamScope,
        event: str,
        data: dict,
        ttl_seconds: int,
    ) -> QueryStreamEvent:
        with self._lock:
            session = self._sessions.get(stream_id)
            if session is None or session.scope != scope:
                raise LookupError("E_QUERY_STREAM_NOT_FOUND:查询流不存在")
            sequence = session.last_sequence + 1
            stored = QueryStreamEvent(
                stream_id=stream_id,
                sequence=sequence,
                event=str(event),
                data=deepcopy(data),
            )
            events = self._events.setdefault(stream_id, [])
            events.append(stored)
            if len(events) > self.max_events:
                del events[: len(events) - self.max_events]
            session.last_sequence = sequence
            session.first_sequence = events[0].sequence if events else sequence + 1
            session.snapshot = apply_stream_event(
                session.snapshot,
                sequence=sequence,
                event=stored.event,
                data=stored.data,
            )
            session.terminal = bool(session.snapshot.get("terminal"))
            session.expires_at = float(self._now()) + max(1, int(ttl_seconds))
            return deepcopy(stored)

    def list_after(
        self,
        *,
        stream_id: str,
        scope: QueryStreamScope,
        after_sequence: int,
        limit: int = 256,
    ) -> list[QueryStreamEvent]:
        with self._lock:
            session = self._sessions.get(stream_id)
            if session is None or session.scope != scope:
                raise LookupError("E_QUERY_STREAM_NOT_FOUND:查询流不存在")
            values = [
                deepcopy(event)
                for event in self._events.get(stream_id, [])
                if event.sequence > int(after_sequence)
            ]
            return values[: max(1, min(int(limit), self.max_events))]

    def clear(self) -> None:
        with self._lock:
            self._scope_index.clear()
            self._sessions.clear()
            self._events.clear()


__all__ = ["InMemoryQueryStreamRepository"]
