"""Resumable v2 query stream built from snapshots and ordered deltas."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi.encoders import jsonable_encoder

from app.domain.query_stream import (
    DURABLE_STREAM_VERSION,
    QueryStreamEvent,
    QueryStreamScope,
    QueryStreamSession,
    sanitize_stream_data,
)
from app.services.query_stream_service import stream_query_events


class QueryStreamCursorError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}:{message}")


@dataclass(frozen=True)
class DurableQueryStreamHandle:
    stream_id: str
    created: bool
    events: AsyncIterator[str]


def encode_durable_stream_event(
    *,
    stream_id: str,
    sequence: int,
    event: str,
    data: dict,
) -> str:
    payload = jsonable_encoder(
        {
            "version": DURABLE_STREAM_VERSION,
            "stream_id": stream_id,
            "sequence": int(sequence),
            "event": event,
            "data": sanitize_stream_data(data),
        }
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"


class DurableQueryStreamService:
    def __init__(
        self,
        *,
        repository,
        chunk_rows: int,
        heartbeat_seconds: float,
        poll_seconds: float = 0.05,
        ttl_seconds: int = 900,
        read_batch_size: int = 256,
    ):
        self.repository = repository
        self.chunk_rows = max(1, int(chunk_rows))
        self.heartbeat_seconds = max(0.001, float(heartbeat_seconds))
        self.poll_seconds = max(0, float(poll_seconds))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.read_batch_size = max(1, int(read_batch_size))
        self._producer_tasks: set[asyncio.Task] = set()
        self._task_lock = threading.Lock()

    async def open(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        operation: str,
        client_request_id: str,
        run_query: Callable[[], Awaitable[dict]],
        after_sequence: int | None = None,
    ) -> DurableQueryStreamHandle:
        if after_sequence is not None and int(after_sequence) < 0:
            raise QueryStreamCursorError(
                "E_QUERY_STREAM_CURSOR_INVALID",
                "after_sequence 不能为负数",
            )
        scope = QueryStreamScope(
            tenant_id=str(tenant_id),
            owner_id=str(owner_id),
            operation=str(operation),
            client_request_id=str(client_request_id),
        )
        session, created = self.repository.claim(
            scope=scope,
            ttl_seconds=self.ttl_seconds,
        )
        if after_sequence is not None and int(after_sequence) > session.last_sequence:
            raise QueryStreamCursorError(
                "E_QUERY_STREAM_CURSOR_AHEAD",
                "after_sequence 超过服务端最新序号",
            )
        cursor_expired = (
            after_sequence is not None
            and int(after_sequence) < session.first_sequence - 1
        )
        initial = session
        if created:
            task = asyncio.create_task(
                self._produce(
                    session=session,
                    scope=scope,
                    run_query=run_query,
                )
            )
            with self._task_lock:
                self._producer_tasks.add(task)
            task.add_done_callback(self._producer_done)
        events = self._consume(
            session=initial,
            scope=scope,
            after_sequence=after_sequence,
            cursor_expired=cursor_expired,
        )
        return DurableQueryStreamHandle(
            stream_id=session.id,
            created=created,
            events=events,
        )

    def _producer_done(self, task: asyncio.Task) -> None:
        with self._task_lock:
            self._producer_tasks.discard(task)
        try:
            task.result()
        except BaseException:
            pass

    async def _produce(
        self,
        *,
        session: QueryStreamSession,
        scope: QueryStreamScope,
        run_query: Callable[[], Awaitable[dict]],
    ) -> None:
        async for encoded in stream_query_events(
            run_query,
            chunk_rows=self.chunk_rows,
            heartbeat_seconds=self.heartbeat_seconds,
        ):
            payload = json.loads(encoded)
            data = payload["data"]
            if payload["event"] in {"started", "progress"}:
                data = {**data, "stream_id": session.id}
            self.repository.append(
                stream_id=session.id,
                scope=scope,
                event=payload["event"],
                data=sanitize_stream_data(data),
                ttl_seconds=self.ttl_seconds,
            )

    async def _consume(
        self,
        *,
        session: QueryStreamSession,
        scope: QueryStreamScope,
        after_sequence: int | None,
        cursor_expired: bool,
    ) -> AsyncIterator[str]:
        if after_sequence is None or cursor_expired:
            reason = "cursor_expired" if cursor_expired else "initial"
            yield encode_durable_stream_event(
                stream_id=session.id,
                sequence=session.last_sequence,
                event="snapshot",
                data={"reason": reason, "state": session.snapshot},
            )
            cursor = session.last_sequence
        else:
            cursor = int(after_sequence)

        while True:
            for event in self.repository.list_after(
                stream_id=session.id,
                scope=scope,
                after_sequence=cursor,
                limit=self.read_batch_size,
            ):
                cursor = event.sequence
                yield self._encode_delta(event)
            current = self.repository.get(stream_id=session.id, scope=scope)
            if current is None:
                raise QueryStreamCursorError(
                    "E_QUERY_STREAM_EXPIRED",
                    "查询流状态已过期",
                )
            if current.terminal and cursor >= current.last_sequence:
                return
            await asyncio.sleep(self.poll_seconds)

    @staticmethod
    def _encode_delta(event: QueryStreamEvent) -> str:
        return encode_durable_stream_event(
            stream_id=event.stream_id,
            sequence=event.sequence,
            event=event.event,
            data=event.data,
        )


__all__ = [
    "DurableQueryStreamHandle",
    "DurableQueryStreamService",
    "QueryStreamCursorError",
    "encode_durable_stream_event",
]
