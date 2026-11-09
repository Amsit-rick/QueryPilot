import pytest

from app import llm


@pytest.mark.anyio
async def test_llm_request_uses_deterministic_generation_settings(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "SELECT 1"}}]}

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, path, **kwargs):
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    assert await llm.generate_sql("查询", {"sales": {"id"}}, "key", "http://model", "deepseek-chat", 5) == "SELECT 1"
    payload = captured["json"]
    assert payload["temperature"] == 0
    assert payload["top_p"] == 1
    assert payload["stream"] is False
    assert llm.PROMPT_VERSION in payload["messages"][0]["content"]


@pytest.mark.anyio
async def test_llm_forwards_provider_usage(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "SELECT 1"}}], "usage": {"prompt_tokens": 12, "completion_tokens": 7}}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, path, **kwargs):
            return Response()

    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    usage = {}
    result = await llm.generate_sql("usage callback test 20260916", {"sales": {"id"}}, "key", "http://model", "deepseek-chat", 5, usage_callback=usage.update)
    assert result == "SELECT 1"
    assert usage == {"input_tokens": 12, "output_tokens": 7}


@pytest.mark.anyio
async def test_llm_preserves_structured_confidence_for_quality_gate(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"sql":"SELECT region FROM sales","intent":"summary","confidence":0.42,"needs_approval":false}'}}]}

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, path, **kwargs):
            return Response()

    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    result = await llm.generate_sql("structured quality plan 20260922", {"sales": {"region"}}, "key", "http://model", "deepseek-chat", 5)
    assert result == {"sql": "SELECT region FROM sales", "params": {}, "intent": "summary", "confidence": 0.42, "needs_approval": False}


@pytest.mark.anyio
async def test_llm_invalid_usage_does_not_fail_successful_generation(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "SELECT 2"}}], "usage": {"prompt_tokens": "bad", "completion_tokens": -3}}

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, path, **kwargs):
            return Response()

    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)
    usage = {}
    result = await llm.generate_sql("invalid usage test 20260916", {"sales": {"id"}}, "key", "http://model", "deepseek-chat", 5, usage_callback=usage.update)
    assert result == "SELECT 2"
    assert usage == {"input_tokens": 0, "output_tokens": 0}
