from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.repositories.memory_query_stream_repository import InMemoryQueryStreamRepository
from app.services.durable_query_stream_service import (
    DurableQueryStreamService,
    QueryStreamCursorError,
)
from app.task_runtime import get_task_service


def _result(task_id: str = "task-stream") -> dict:
    return {
        "task_id": task_id,
        "trace_id": "trace-stream",
        "question": "按区域查询",
        "follow_up": False,
        "answer": "查询完成，共返回 2 行。",
        "sql": "SELECT region, amount FROM sales",
        "columns": ["region", "amount"],
        "rows": [["华东", 60], ["华南", 40]],
        "row_count": 2,
        "truncated": False,
        "analysis": None,
    }


async def _collect(iterator):
    return [json.loads(line) async for line in iterator]


def _service(*, max_events: int = 100) -> DurableQueryStreamService:
    return DurableQueryStreamService(
        repository=InMemoryQueryStreamRepository(max_events=max_events),
        chunk_rows=1,
        heartbeat_seconds=0.01,
        poll_seconds=0,
    )


def test_v2_stream_starts_with_snapshot_then_monotonic_deltas():
    calls = 0
    service = _service()

    async def run_query():
        nonlocal calls
        calls += 1
        return _result()

    async def scenario():
        handle = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=run_query,
        )
        return handle, await _collect(handle.events)

    handle, events = asyncio.run(scenario())

    assert calls == 1
    assert handle.created is True
    assert events[0]["version"] == "2"
    assert events[0]["event"] == "snapshot"
    assert events[0]["sequence"] == 0
    assert events[0]["data"]["state"]["status"] == "running"
    deltas = events[1:]
    assert [item["sequence"] for item in deltas] == list(range(1, len(deltas) + 1))
    assert [item["event"] for item in deltas if item["event"] != "progress"] == [
        "started",
        "result",
        "rows",
        "rows",
        "completed",
    ]
    assert len({item["stream_id"] for item in events}) == 1


def test_reconnect_replays_only_events_after_cursor_without_rerunning_query():
    calls = 0
    service = _service()

    async def run_query():
        nonlocal calls
        calls += 1
        return _result()

    async def scenario():
        first = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=run_query,
        )
        first_events = await _collect(first.events)
        resumed = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=run_query,
            after_sequence=2,
        )
        return first, first_events, resumed, await _collect(resumed.events)

    first, first_events, resumed, resumed_events = asyncio.run(scenario())

    assert calls == 1
    assert resumed.created is False
    assert resumed.stream_id == first.stream_id
    expected = [item for item in first_events if item["sequence"] > 2]
    assert resumed_events == expected
    assert resumed_events[-1]["event"] == "completed"


def test_connection_can_close_while_producer_finishes_and_snapshot_recovers_state():
    calls = 0
    service = _service()

    async def run_query():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return _result()

    async def scenario():
        first = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=run_query,
        )
        iterator = first.events.__aiter__()
        snapshot = json.loads(await anext(iterator))
        await iterator.aclose()
        await asyncio.sleep(0.05)
        resumed = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=run_query,
        )
        recovered = await _collect(resumed.events)
        return snapshot, recovered

    snapshot, recovered = asyncio.run(scenario())

    assert snapshot["event"] == "snapshot"
    assert calls == 1
    assert len(recovered) == 1
    assert recovered[0]["event"] == "snapshot"
    state = recovered[0]["data"]["state"]
    assert state["status"] == "completed"
    assert state["rows"] == [["华东", 60], ["华南", 40]]
    assert state["terminal"] is True


def test_trimmed_cursor_falls_back_to_complete_snapshot():
    service = _service(max_events=2)

    async def scenario():
        first = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=lambda: asyncio.sleep(0, result=_result()),
        )
        await _collect(first.events)
        resumed = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=lambda: asyncio.sleep(0, result=_result()),
            after_sequence=0,
        )
        return await _collect(resumed.events)

    events = asyncio.run(scenario())

    assert len(events) == 1
    assert events[0]["event"] == "snapshot"
    assert events[0]["data"]["reason"] == "cursor_expired"
    assert events[0]["data"]["state"]["status"] == "completed"


def test_cursor_ahead_of_stream_fails_before_response_starts():
    service = _service()

    async def scenario():
        first = await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=lambda: asyncio.sleep(0, result=_result()),
        )
        await _collect(first.events)
        await service.open(
            tenant_id="tenant-a",
            owner_id="user-a",
            operation="query",
            client_request_id="request-a",
            run_query=lambda: asyncio.sleep(0, result=_result()),
            after_sequence=999,
        )

    try:
        asyncio.run(scenario())
    except QueryStreamCursorError as exc:
        assert exc.code == "E_QUERY_STREAM_CURSOR_AHEAD"
    else:
        raise AssertionError("cursor ahead must fail before streaming")


def test_same_client_request_id_is_isolated_by_tenant_and_owner():
    calls = 0
    service = _service()

    async def run_query():
        nonlocal calls
        calls += 1
        return _result(task_id=f"task-{calls}")

    async def scenario():
        handles = []
        for tenant_id, owner_id in (
            ("tenant-a", "user-a"),
            ("tenant-b", "user-a"),
            ("tenant-a", "user-b"),
        ):
            handle = await service.open(
                tenant_id=tenant_id,
                owner_id=owner_id,
                operation="query",
                client_request_id="shared-request",
                run_query=run_query,
            )
            await _collect(handle.events)
            handles.append(handle)
        return handles

    handles = asyncio.run(scenario())

    assert calls == 3
    assert len({handle.stream_id for handle in handles}) == 3


def _v2_events(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line]


def test_v2_query_api_reconnects_by_sequence_without_duplicate_task_side_effects():
    client = TestClient(app)
    client_request_id = f"stream-v2-{uuid4()}"
    payload = {
        "client_request_id": client_request_id,
        "question": "查询区域",
        "sql": "SELECT region FROM sales ORDER BY id",
        "max_rows": 2,
    }
    headers = {"X-Query-Stream-Version": "2"}

    with client.stream("POST", "/api/v1/query/stream", json=payload, headers=headers) as first:
        first_events = _v2_events(first)
    task_id = first_events[-2]["data"].get("task_id") or first_events[1]["data"].get("task_id")
    result_event = next(item for item in first_events if item["event"] == "result")
    task_id = result_event["data"]["task_id"]
    detail_before = get_task_service().detail_for_tenant(task_id=task_id, tenant_id="default")
    counts_before = tuple(len(detail_before[name]) for name in ("steps", "events", "artifacts"))

    with client.stream(
        "POST",
        "/api/v1/query/stream?after_sequence=2",
        json=payload,
        headers=headers,
    ) as resumed:
        resumed_events = _v2_events(resumed)
    detail_after = get_task_service().detail_for_tenant(task_id=task_id, tenant_id="default")

    assert first.status_code == resumed.status_code == 200
    assert first.headers["x-query-stream-id"] == resumed.headers["x-query-stream-id"]
    assert all(item["version"] == "2" for item in first_events + resumed_events)
    assert all(item["sequence"] > 2 for item in resumed_events)
    assert tuple(len(detail_after[name]) for name in ("steps", "events", "artifacts")) == counts_before


def test_v2_query_api_requires_client_request_id_and_rejects_future_cursor():
    client = TestClient(app)
    missing = client.post(
        "/api/v1/query/stream",
        json={"question": "查询区域", "sql": "SELECT region FROM sales"},
        headers={"X-Query-Stream-Version": "2"},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "E_QUERY_STREAM_ID_REQUIRED"

    payload = {
        "client_request_id": f"stream-v2-cursor-{uuid4()}",
        "question": "查询区域",
        "sql": "SELECT region FROM sales",
    }
    with client.stream(
        "POST",
        "/api/v1/query/stream",
        json=payload,
        headers={"X-Query-Stream-Version": "2"},
    ) as response:
        _v2_events(response)
    ahead = client.post(
        "/api/v1/query/stream?after_sequence=999",
        json=payload,
        headers={"X-Query-Stream-Version": "2"},
    )
    assert ahead.status_code == 409
    assert ahead.json()["detail"]["code"] == "E_QUERY_STREAM_CURSOR_AHEAD"

