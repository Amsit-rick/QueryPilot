"""Versioned NDJSON transport for completed Query Pilot responses.

This module streams transport events without creating a second query path.
The supplied query coroutine remains responsible for authorization, SQL
validation, approval, execution, redaction, persistence, and audit logging.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import uuid4

from fastapi.encoders import jsonable_encoder


STREAM_VERSION = "1"


def encode_stream_event(event: str, data: dict) -> str:
    payload = jsonable_encoder({"version": STREAM_VERSION, "event": event, "data": data})
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def _consume_background_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except BaseException:
        pass


async def stream_query_events(
    run_query: Callable[[], Awaitable[dict]],
    *,
    chunk_rows: int,
    heartbeat_seconds: float = 5.0,
) -> AsyncIterator[str]:
    if chunk_rows < 1:
        raise ValueError("chunk_rows must be positive")
    stream_id = str(uuid4())
    yield encode_stream_event("started", {"stream_id": stream_id})

    query_task = asyncio.create_task(run_query())
    started = asyncio.get_running_loop().time()
    try:
        while not query_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(query_task), timeout=heartbeat_seconds)
            except TimeoutError:
                elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1000)
                yield encode_stream_event("progress", {"stream_id": stream_id, "phase": "querying", "elapsed_ms": elapsed_ms})
        result = query_task.result()
    except asyncio.CancelledError:
        query_task.add_done_callback(_consume_background_result)
        raise
    except Exception:
        yield encode_stream_event(
            "error",
            {
                "stream_id": stream_id,
                "error": {
                    "code": "E_QUERY_STREAM",
                    "message": "查询流处理失败，请联系管理员并提供 stream_id",
                },
            },
        )
        return

    if result.get("status") == "approval_required":
        yield encode_stream_event("approval_required", result)
        return
    if result.get("error"):
        yield encode_stream_event("error", result)
        return

    rows = list(result.get("rows", []))
    metadata = {key: value for key, value in result.items() if key != "rows"}
    yield encode_stream_event("result", metadata)
    chunk_count = 0
    for offset in range(0, len(rows), chunk_rows):
        chunk_count += 1
        yield encode_stream_event("rows", {"offset": offset, "rows": rows[offset : offset + chunk_rows]})
        await asyncio.sleep(0)
    yield encode_stream_event(
        "completed",
        {
            "row_count": int(result.get("row_count", len(rows))),
            "chunk_count": chunk_count,
            "truncated": bool(result.get("truncated", False)),
        },
    )


__all__ = ["STREAM_VERSION", "encode_stream_event", "stream_query_events"]
