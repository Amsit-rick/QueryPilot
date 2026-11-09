"""Process-local schema embedding runtime with explicit safe configuration."""

from __future__ import annotations

import threading

from app.services.schema_retrieval_service import EmbeddingScorer
from app.services.schema_embedding_service import (
    EmbeddingProviderError,
    OpenAICompatibleSchemaEmbeddingScorer,
    SchemaEmbeddingConfig,
)


_lock = threading.Lock()
_scorer: EmbeddingScorer | None = None
_configured = False
_source = "disabled"
_configuration_error = ""


def _configure_from_settings() -> None:
    global _scorer, _configured, _source, _configuration_error
    from app.config import settings

    provider = str(settings.schema_embedding_provider).strip().lower()
    if provider == "disabled":
        _scorer = None
        _source = "disabled"
        _configuration_error = ""
    elif provider == "openai_compatible":
        try:
            _scorer = OpenAICompatibleSchemaEmbeddingScorer(
                SchemaEmbeddingConfig(
                    base_url=settings.schema_embedding_base_url,
                    model=settings.schema_embedding_model,
                    api_key_env=settings.schema_embedding_api_key_env,
                    timeout_seconds=settings.schema_embedding_timeout_seconds,
                    batch_size=settings.schema_embedding_batch_size,
                    cache_entries=settings.schema_embedding_cache_entries,
                    max_documents=settings.schema_embedding_max_documents,
                    max_document_chars=settings.schema_embedding_max_document_chars,
                    max_total_chars=settings.schema_embedding_max_total_chars,
                    cache_backend=settings.schema_embedding_cache_backend,
                    cache_ttl_seconds=settings.schema_embedding_cache_ttl_seconds,
                    redis_url=settings.redis_url,
                    circuit_backend=settings.schema_embedding_circuit_backend,
                    circuit_failure_threshold=settings.schema_embedding_circuit_failures,
                    circuit_reset_seconds=settings.schema_embedding_circuit_reset_seconds,
                )
            )
            _source = "environment"
            _configuration_error = ""
        except EmbeddingProviderError as exc:
            _scorer = None
            _source = "environment"
            _configuration_error = exc.code
    else:
        _scorer = None
        _source = "environment"
        _configuration_error = "E_EMBEDDING_CONFIG"
    _configured = True


def get_schema_embedding_scorer() -> EmbeddingScorer | None:
    global _configured
    with _lock:
        if not _configured:
            _configure_from_settings()
        return _scorer


def set_schema_embedding_scorer(scorer: EmbeddingScorer | None) -> None:
    global _scorer, _configured, _source, _configuration_error
    if scorer is not None and not callable(scorer):
        raise TypeError("schema embedding scorer must be callable")
    with _lock:
        _scorer = scorer
        _configured = True
        _source = "manual" if scorer is not None else "disabled"
        _configuration_error = ""


def schema_embedding_runtime_status() -> dict[str, object]:
    scorer = get_schema_embedding_scorer()
    with _lock:
        source = _source
        configuration_error = _configuration_error
    if scorer is None:
        return {
            "provider": "disabled" if source == "disabled" else "openai_compatible",
            "source": source,
            "status": "disabled" if source == "disabled" else "degraded",
            "configured": False,
            "last_error_code": configuration_error,
            "cached_documents": 0,
        }
    status_method = getattr(scorer, "status", None)
    status = status_method() if callable(status_method) else {"status": "ready", "configured": True}
    return {"source": source, **status}


def reset_schema_embedding_runtime_for_tests() -> None:
    global _scorer, _configured, _source, _configuration_error
    with _lock:
        _scorer = None
        _configured = False
        _source = "disabled"
        _configuration_error = ""


__all__ = [
    "get_schema_embedding_scorer",
    "reset_schema_embedding_runtime_for_tests",
    "schema_embedding_runtime_status",
    "set_schema_embedding_scorer",
]
