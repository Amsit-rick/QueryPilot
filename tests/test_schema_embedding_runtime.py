from app.schema_embedding_runtime import (
    get_schema_embedding_scorer,
    reset_schema_embedding_runtime_for_tests,
    schema_embedding_runtime_status,
)


def test_runtime_builds_configured_provider_without_exposing_secret(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schema_embedding_provider", "openai_compatible")
    monkeypatch.setattr(settings, "schema_embedding_base_url", "https://embedding.example.com/v1")
    monkeypatch.setattr(settings, "schema_embedding_model", "embedding-model")
    monkeypatch.setattr(settings, "schema_embedding_api_key_env", "SCHEMA_EMBEDDING_RUNTIME_KEY")
    monkeypatch.setenv("SCHEMA_EMBEDDING_RUNTIME_KEY", "runtime-secret")
    reset_schema_embedding_runtime_for_tests()
    try:
        assert get_schema_embedding_scorer() is not None
        status = schema_embedding_runtime_status()
        assert status["provider"] == "openai_compatible"
        assert status["configured"] is True
        assert status["status"] == "ready"
        assert "runtime-secret" not in str(status)
    finally:
        reset_schema_embedding_runtime_for_tests()


def test_runtime_defaults_to_disabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "schema_embedding_provider", "disabled")
    reset_schema_embedding_runtime_for_tests()
    try:
        assert get_schema_embedding_scorer() is None
        assert schema_embedding_runtime_status()["status"] == "disabled"
    finally:
        reset_schema_embedding_runtime_for_tests()
