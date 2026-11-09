import asyncio
import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.query_stream_service import stream_query_events


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line]


def test_query_stream_emits_versioned_row_chunks_without_changing_query_contract(monkeypatch):
    monkeypatch.setattr(settings, "query_stream_chunk_rows", 1)
    client = TestClient(app)

    with client.stream(
        "POST",
        "/api/v1/query/stream",
            json={"question": "流式查询", "sql": "SELECT region FROM sales ORDER BY id", "max_rows": 2},
    ) as response:
        events = _events(response)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert [item["event"] for item in events] == ["started", "result", "rows", "rows", "completed"]
    assert all(item["version"] == "1" for item in events)
    assert "rows" not in events[1]["data"]
    assert events[1]["data"]["row_count"] == 2
    streamed_rows = [row for event in events if event["event"] == "rows" for row in event["data"]["rows"]]
    assert len(streamed_rows) == 2
    assert events[-1]["data"] == {"row_count": 2, "chunk_count": 2, "truncated": True}


def test_query_stream_returns_terminal_error_event_without_rows():
    with TestClient(app).stream(
        "POST",
        "/api/v1/query/stream",
        json={"question": "非法查询", "sql": "DELETE FROM sales"},
    ) as response:
        events = _events(response)

    assert [item["event"] for item in events] == ["started", "error"]
    assert events[-1]["data"]["error"]["code"] == "E_READONLY_VIOLATION"
    assert not any(item["event"] == "rows" for item in events)


def test_query_stream_returns_approval_as_terminal_event():
    with TestClient(app).stream(
        "POST",
        "/api/v1/query/stream",
        json={"question": "审批查询", "sql": "SELECT region FROM sales", "require_approval": True},
    ) as response:
        events = _events(response)

    assert [item["event"] for item in events] == ["started", "approval_required"]
    assert events[-1]["data"]["status"] == "approval_required"
    assert events[-1]["data"]["approval_id"]


def test_query_endpoint_hides_unexpected_postprocess_exception(monkeypatch):
    class ExplodingGraph:
        async def ainvoke(self, _state):
            raise RuntimeError("password=super-secret host=db.internal")

    monkeypatch.setattr("app.main.create_query_graph", lambda **_kwargs: ExplodingGraph())

    response = TestClient(app).post(
        "/api/v1/query",
        json={"question": "异常脱敏", "sql": "SELECT region FROM sales", "max_rows": 1},
    )
    payload = response.json()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert response.status_code == 200
    assert payload["error"]["code"] == "E_AGENT_POSTPROCESS"
    assert "trace_id" in payload["error"]["message"]
    assert "super-secret" not in rendered
    assert "db.internal" not in rendered
    assert "RuntimeError" not in rendered


def test_query_stream_hides_unexpected_transport_exception():
    async def explode():
        raise RuntimeError("api_key=super-secret host=db.internal")

    async def collect():
        return [json.loads(line) async for line in stream_query_events(explode, chunk_rows=1)]

    events = asyncio.run(collect())
    rendered = json.dumps(events, ensure_ascii=False)

    assert [item["event"] for item in events] == ["started", "error"]
    assert events[-1]["data"]["error"]["code"] == "E_QUERY_STREAM"
    assert "super-secret" not in rendered
    assert "db.internal" not in rendered
    assert "RuntimeError" not in rendered
