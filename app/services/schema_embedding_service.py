"""OpenAI-compatible schema embedding scorer with bounded in-process caching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
import threading
from time import perf_counter
from typing import Mapping
from urllib.parse import urlparse

import httpx

from app.circuit_breaker import (
    CircuitBackendUnavailable,
    allow_request,
    record_failure,
    record_success,
)


class EmbeddingProviderError(RuntimeError):
    """Stable public failure without upstream response or credential details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SchemaEmbeddingConfig:
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 10.0
    batch_size: int = 64
    cache_entries: int = 10000
    max_documents: int = 5000
    max_document_chars: int = 4000
    max_total_chars: int = 2000000
    cache_backend: str = "memory"
    cache_ttl_seconds: int = 86400
    redis_url: str = ""
    circuit_backend: str = "memory"
    circuit_failure_threshold: int = 3
    circuit_reset_seconds: int = 30


class RedisSchemaEmbeddingCache:
    """Shared vector cache that never stores raw question or schema text."""

    def __init__(self, redis_url: str, namespace: str, ttl_seconds: int) -> None:
        if not redis_url.strip():
            raise EmbeddingProviderError("E_EMBEDDING_CACHE_CONFIG")
        self.redis_url = redis_url
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds

    def _client(self):
        try:
            import redis

            return redis.Redis.from_url(
                self.redis_url,
                decode_responses=False,
                socket_connect_timeout=0.25,
                socket_timeout=0.25,
            )
        except Exception as exc:
            raise EmbeddingProviderError("E_EMBEDDING_CACHE_UNAVAILABLE") from exc

    def _key(self, document_hash: str) -> str:
        return f"query-pilot:schema-embedding:{self.namespace}:{document_hash}"

    def get(self, document_hash: str) -> tuple[float, ...] | None:
        try:
            raw = self._client().get(self._key(document_hash))
            if raw is None:
                return None
            if len(raw) > 256 * 1024:
                raise EmbeddingProviderError("E_EMBEDDING_CACHE_INVALID")
            payload = json.loads(raw)
            if not isinstance(payload, list) or not 1 <= len(payload) <= 16384:
                raise EmbeddingProviderError("E_EMBEDDING_CACHE_INVALID")
            vector = tuple(float(value) for value in payload)
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingProviderError("E_EMBEDDING_CACHE_INVALID")
            return vector
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError("E_EMBEDDING_CACHE_UNAVAILABLE") from exc

    def set(self, document_hash: str, vector: tuple[float, ...]) -> None:
        try:
            payload = json.dumps(vector, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(payload) > 256 * 1024:
                raise EmbeddingProviderError("E_EMBEDDING_CACHE_INVALID")
            self._client().setex(self._key(document_hash), self.ttl_seconds, payload)
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError("E_EMBEDDING_CACHE_UNAVAILABLE") from exc


class OpenAICompatibleSchemaEmbeddingScorer:
    """Embed authorized schema documents and return normalized cosine scores.

    Document vectors are cached by model and content hash. Query vectors are
    intentionally not cached because user questions may contain sensitive data.
    """

    def __init__(
        self,
        config: SchemaEmbeddingConfig,
        *,
        environ: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(config.base_url.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise EmbeddingProviderError("E_EMBEDDING_CONFIG")
        if not config.model.strip() or not config.api_key_env.strip():
            raise EmbeddingProviderError("E_EMBEDDING_CONFIG")
        self.config = config
        self._base_url = config.base_url.strip().rstrip("/")
        self._environ = environ if environ is not None else os.environ
        self._transport = transport
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._lock = threading.Lock()
        self._last_error_code = ""
        if config.cache_backend not in {"memory", "redis"} or config.circuit_backend not in {"memory", "redis"}:
            raise EmbeddingProviderError("E_EMBEDDING_CONFIG")
        namespace = sha256(f"{self._base_url}\0{config.model}".encode("utf-8")).hexdigest()[:24]
        self._shared_cache = (
            RedisSchemaEmbeddingCache(config.redis_url, namespace, config.cache_ttl_seconds)
            if config.cache_backend == "redis"
            else None
        )
        self._circuit_key = f"schema-embedding:{namespace}"

    @property
    def last_error_code(self) -> str:
        return self._last_error_code

    def status(self) -> dict[str, object]:
        with self._lock:
            cached = len(self._cache)
        return {
            "provider": "openai_compatible",
            "configured": bool(self._environ.get(self.config.api_key_env, "").strip()),
            "status": "ready" if self._environ.get(self.config.api_key_env, "").strip() else "unconfigured",
            "last_error_code": self._last_error_code,
            "cached_documents": cached,
            "cache_backend": self.config.cache_backend,
            "circuit_backend": self.config.circuit_backend,
        }

    def __call__(self, question: str, documents: dict[str, str]) -> dict[str, float]:
        started = perf_counter()
        cache_hits = 0
        cache_misses = 0
        try:
            if len(documents) > self.config.max_documents:
                raise EmbeddingProviderError("E_EMBEDDING_DOCUMENT_LIMIT")
            normalized_documents = {
                table: str(raw_document)[: self.config.max_document_chars]
                for table, raw_document in documents.items()
            }
            if sum(len(document) for document in normalized_documents.values()) > self.config.max_total_chars:
                raise EmbeddingProviderError("E_EMBEDDING_CHARACTER_BUDGET")
            vectors: dict[str, tuple[float, ...]] = {}
            missing: list[tuple[str, str, str]] = []
            for table, document in normalized_documents.items():
                key = self._cache_key(document)
                cached = self._get_cached(key)
                if cached is None:
                    cache_misses += 1
                    missing.append((table, document, key))
                else:
                    cache_hits += 1
                    vectors[table] = cached
            query_vector = self._embed_texts([str(question)])[0]
            for offset in range(0, len(missing), self.config.batch_size):
                batch = missing[offset : offset + self.config.batch_size]
                embedded = self._embed_texts([document for _table, document, _key in batch])
                for (table, _document, key), vector in zip(batch, embedded, strict=True):
                    self._set_cached(key, vector)
                    vectors[table] = vector
            scores = {
                table: round(max(0.0, min(1.0, self._cosine(query_vector, vector))), 8)
                for table, vector in vectors.items()
            }
            self._last_error_code = ""
            self._record_metric("ok", started, len(documents), cache_hits, cache_misses)
            return scores
        except EmbeddingProviderError as exc:
            self._last_error_code = exc.code
            self._record_metric("error", started, len(documents), cache_hits, cache_misses)
            raise
        except Exception as exc:
            self._last_error_code = "E_EMBEDDING_UNAVAILABLE"
            self._record_metric("error", started, len(documents), cache_hits, cache_misses)
            raise EmbeddingProviderError("E_EMBEDDING_UNAVAILABLE") from exc

    def _embed_texts(self, texts: list[str]) -> list[tuple[float, ...]]:
        api_key = self._environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            raise EmbeddingProviderError("E_EMBEDDING_SECRET_MISSING")
        circuit_redis_url = self.config.redis_url if self.config.circuit_backend == "redis" else None
        try:
            allow_request(
                key=self._circuit_key,
                threshold=self.config.circuit_failure_threshold,
                reset_seconds=self.config.circuit_reset_seconds,
                redis_url=circuit_redis_url,
            )
        except CircuitBackendUnavailable as exc:
            raise EmbeddingProviderError("E_EMBEDDING_CIRCUIT_UNAVAILABLE") from exc
        except RuntimeError as exc:
            raise EmbeddingProviderError("E_EMBEDDING_CIRCUIT_OPEN") from exc
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds,
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = client.post(
                    f"{self._base_url}/embeddings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": self.config.model, "input": texts, "encoding_format": "float"},
                )
                response.raise_for_status()
                payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(texts):
                raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
            indexed: dict[int, tuple[float, ...]] = {}
            for item in data:
                if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                    raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
                raw_vector = item.get("embedding")
                if not isinstance(raw_vector, list) or not 1 <= len(raw_vector) <= 16384:
                    raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
                try:
                    vector = tuple(float(value) for value in raw_vector)
                except (TypeError, ValueError) as exc:
                    raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID") from exc
                if not all(math.isfinite(value) for value in vector):
                    raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
                indexed[item["index"]] = vector
            if set(indexed) != set(range(len(texts))):
                raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
            vectors = [indexed[index] for index in range(len(texts))]
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1:
                raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
        except Exception as exc:
            try:
                record_failure(
                    key=self._circuit_key,
                    threshold=self.config.circuit_failure_threshold,
                    reset_seconds=self.config.circuit_reset_seconds,
                    redis_url=circuit_redis_url,
                )
            except CircuitBackendUnavailable as circuit_exc:
                raise EmbeddingProviderError("E_EMBEDDING_CIRCUIT_UNAVAILABLE") from circuit_exc
            if isinstance(exc, EmbeddingProviderError):
                raise
            raise EmbeddingProviderError("E_EMBEDDING_REQUEST_FAILED") from exc
        try:
            record_success(
                key=self._circuit_key,
                threshold=self.config.circuit_failure_threshold,
                reset_seconds=self.config.circuit_reset_seconds,
                redis_url=circuit_redis_url,
            )
        except CircuitBackendUnavailable as exc:
            raise EmbeddingProviderError("E_EMBEDDING_CIRCUIT_UNAVAILABLE") from exc
        return vectors

    def _cache_key(self, document: str) -> str:
        return sha256(f"{self.config.model}\0{document}".encode("utf-8")).hexdigest()

    def _get_cached(self, key: str) -> tuple[float, ...] | None:
        with self._lock:
            vector = self._cache.get(key)
            if vector is not None:
                self._cache.move_to_end(key)
                return vector
        if self._shared_cache is None:
            return None
        vector = self._shared_cache.get(key)
        if vector is not None:
            self._set_local_cached(key, vector)
        return vector

    def _set_cached(self, key: str, vector: tuple[float, ...]) -> None:
        if self._shared_cache is not None:
            self._shared_cache.set(key, vector)
        self._set_local_cached(key, vector)

    def _set_local_cached(self, key: str, vector: tuple[float, ...]) -> None:
        with self._lock:
            self._cache[key] = vector
            self._cache.move_to_end(key)
            while len(self._cache) > self.config.cache_entries:
                self._cache.popitem(last=False)

    @staticmethod
    def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        if len(left) != len(right):
            raise EmbeddingProviderError("E_EMBEDDING_RESPONSE_INVALID")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)

    @staticmethod
    def _record_metric(status: str, started: float, documents: int, cache_hits: int, cache_misses: int) -> None:
        try:
            from app.metrics_runtime import record_schema_embedding_request

            record_schema_embedding_request(
                status=status,
                duration_seconds=max(0.0, perf_counter() - started),
                documents=documents,
                cache_hits=cache_hits,
                cache_misses=cache_misses,
            )
        except Exception:
            return


__all__ = [
    "EmbeddingProviderError",
    "OpenAICompatibleSchemaEmbeddingScorer",
    "SchemaEmbeddingConfig",
]
