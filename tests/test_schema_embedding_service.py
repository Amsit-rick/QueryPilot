import httpx
import pytest
import sys
import types

from app import circuit_breaker
from app.services.schema_embedding_service import (
    EmbeddingProviderError,
    OpenAICompatibleSchemaEmbeddingScorer,
    SchemaEmbeddingConfig,
)


def _config(**changes):
    values = {
        "base_url": "https://embedding.example.com/v1",
        "model": "embedding-model",
        "api_key_env": "EMBEDDING_TEST_KEY",
        "batch_size": 8,
        "cache_entries": 16,
        "max_documents": 20,
    }
    values.update(changes)
    return SchemaEmbeddingConfig(**values)


def test_openai_compatible_scorer_uses_secret_reference_and_document_cache():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret"
        payload = __import__("json").loads(request.content)
        calls.append(payload["input"])
        data = []
        for index, text in enumerate(payload["input"]):
            vector = [0.0, 1.0] if "回款" in text else [1.0, 0.0]
            data.append({"index": index, "embedding": vector})
        return httpx.Response(200, json={"data": data})

    scorer = OpenAICompatibleSchemaEmbeddingScorer(
        _config(),
        environ={"EMBEDDING_TEST_KEY": "test-secret"},
        transport=httpx.MockTransport(handler),
    )
    documents = {"customers": "客户 customer", "payments": "回款 payment"}

    first = scorer("客户数量", documents)
    second = scorer("客户数量", documents)

    assert first["customers"] == 1.0
    assert first["payments"] == 0.0
    assert second == first
    assert len(calls) == 3
    assert scorer.status()["cached_documents"] == 2
    assert "test-secret" not in str(scorer.status())


def test_embedding_scorer_fails_closed_without_exposing_missing_secret():
    scorer = OpenAICompatibleSchemaEmbeddingScorer(_config(), environ={})

    with pytest.raises(EmbeddingProviderError) as error:
        scorer("客户数量", {"customers": "客户 customer"})

    assert error.value.code == "E_EMBEDDING_SECRET_MISSING"
    assert scorer.status()["last_error_code"] == "E_EMBEDDING_SECRET_MISSING"


def test_embedding_scorer_rejects_invalid_or_oversized_provider_payloads():
    def invalid_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": ["not-a-number"]}]})

    scorer = OpenAICompatibleSchemaEmbeddingScorer(
        _config(max_documents=1),
        environ={"EMBEDDING_TEST_KEY": "secret"},
        transport=httpx.MockTransport(invalid_handler),
    )
    with pytest.raises(EmbeddingProviderError, match="E_EMBEDDING_DOCUMENT_LIMIT"):
        scorer("q", {"a": "a", "b": "b"})
    with pytest.raises(EmbeddingProviderError, match="E_EMBEDDING_RESPONSE_INVALID"):
        scorer("q", {"a": "a"})


def test_embedding_character_budget_fails_before_external_request():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    scorer = OpenAICompatibleSchemaEmbeddingScorer(
        _config(model="budget-model", max_total_chars=3),
        environ={"EMBEDDING_TEST_KEY": "secret"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmbeddingProviderError, match="E_EMBEDDING_CHARACTER_BUDGET"):
        scorer("q", {"a": "four"})
    assert called is False


def test_embedding_circuit_opens_after_provider_failure():
    circuit_breaker.reset_memory()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    scorer = OpenAICompatibleSchemaEmbeddingScorer(
        _config(model="circuit-model", circuit_failure_threshold=1),
        environ={"EMBEDDING_TEST_KEY": "secret"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmbeddingProviderError, match="E_EMBEDDING_REQUEST_FAILED"):
        scorer("q", {"a": "a"})
    with pytest.raises(EmbeddingProviderError, match="E_EMBEDDING_CIRCUIT_OPEN"):
        scorer("q", {"a": "a"})
    assert calls == 1


def test_redis_vector_cache_is_shared_without_storing_raw_schema(monkeypatch):
    class FakeRedis:
        values = {}

        def get(self, key):
            return self.values.get(key)

        def setex(self, key, ttl, value):
            assert ttl == 600
            self.values[key] = value

    fake = FakeRedis()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: fake)),
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        calls.append(payload["input"])
        return httpx.Response(
            200,
            json={"data": [{"index": index, "embedding": [1.0, 0.0]} for index, _ in enumerate(payload["input"])]},
        )

    config = _config(
        model="redis-cache-model",
        cache_backend="redis",
        cache_ttl_seconds=600,
        redis_url="redis://shared",
    )
    documents = {"customers": "customer private schema"}
    first = OpenAICompatibleSchemaEmbeddingScorer(
        config,
        environ={"EMBEDDING_TEST_KEY": "secret"},
        transport=httpx.MockTransport(handler),
    )
    second = OpenAICompatibleSchemaEmbeddingScorer(
        config,
        environ={"EMBEDDING_TEST_KEY": "secret"},
        transport=httpx.MockTransport(handler),
    )

    first("q", documents)
    second("q", documents)

    assert len(calls) == 3
    assert all("customer private schema" not in key for key in fake.values)
    assert all(b"customer private schema" not in value for value in fake.values.values())


def test_required_redis_cache_failure_prevents_provider_cost(monkeypatch):
    class BrokenRedis:
        def get(self, _key):
            raise OSError("redis down")

    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: BrokenRedis())),
    )
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    scorer = OpenAICompatibleSchemaEmbeddingScorer(
        _config(model="redis-failure-model", cache_backend="redis", redis_url="redis://shared"),
        environ={"EMBEDDING_TEST_KEY": "secret"},
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(EmbeddingProviderError, match="E_EMBEDDING_CACHE_UNAVAILABLE"):
        scorer("q", {"customers": "customer schema"})
    assert called is False
