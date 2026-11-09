import asyncio
import json

from app import main as main_module
from app.schemas.queries import QueryRequest
from app.services.query_application_service import _question_mentions_column


def test_question_column_matching_uses_identifier_boundaries():
    assert _question_mentions_column("top region by amount", "region") is True
    assert _question_mentions_column("paid orders", "id") is False


def test_query_route_delegates_to_application_service(monkeypatch):
    calls = []

    class FakeQueryApplicationService:
        async def execute(self, request, identity):
            calls.append((request, identity))
            return {"trace_id": "trace-1", "rows": []}

    service = FakeQueryApplicationService()
    monkeypatch.setattr(main_module, "get_query_application_service", lambda: service)

    request = QueryRequest(question="查询销售额", sql="SELECT amount FROM sales")
    identity = {"subject": "1", "tenant_id": "default", "roles": ["admin"]}

    result = asyncio.run(main_module.query(request, identity))

    assert result == {"trace_id": "trace-1", "rows": []}
    assert calls == [(request, identity)]


def test_stream_route_reuses_the_same_application_service(monkeypatch):
    calls = []

    class FakeQueryApplicationService:
        async def execute(self, request, identity):
            calls.append((request.question, identity["subject"]))
            return {
                "trace_id": "trace-stream",
                "columns": ["amount"],
                "rows": [[100]],
                "row_count": 1,
                "truncated": False,
            }

    monkeypatch.setattr(
        main_module,
        "get_query_application_service",
        lambda: FakeQueryApplicationService(),
    )
    request = QueryRequest(question="查询销售额", sql="SELECT amount FROM sales")
    identity = {"subject": "1", "tenant_id": "default", "roles": ["admin"]}

    async def collect_events():
        response = await main_module.query_stream(request, identity)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return [json.loads(line) for line in "".join(chunks).splitlines()]

    events = asyncio.run(collect_events())

    assert calls == [("查询销售额", "1")]
    assert [event["event"] for event in events] == [
        "started",
        "result",
        "rows",
        "completed",
    ]
