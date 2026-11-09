import asyncio

from app.model_defaults import SQL_SAFETY_PROMPT
from app.repositories.memory_model_operations_repository import InMemoryModelOperationsRepository
from app.services.model_operations_service import ModelOperationsService


def provider_payload(**overrides):
    return {
        "name": "DeepSeek Primary",
        "adapter": "direct",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "TENANT_MODEL_API_KEY",
        "enabled": True,
        "is_default": True,
        **overrides,
    }


def test_provider_and_prompt_resolution_is_tenant_scoped_and_secret_referenced():
    repository = InMemoryModelOperationsRepository()
    service = ModelOperationsService(repository, environ={"TENANT_MODEL_API_KEY": "secret-value"})
    provider = service.create_provider(tenant_id="tenant-a", **provider_payload())
    service.create_provider(tenant_id="tenant-b", **provider_payload(name="Tenant B"))
    first = service.create_prompt(tenant_id="tenant-a", owner_id="admin", name="sql_generation", system_prompt="优先使用清晰的字段别名。")
    second = service.create_prompt(tenant_id="tenant-a", owner_id="admin", name="sql_generation", system_prompt="使用业务字段说明。")

    assert provider.api_key_env == "TENANT_MODEL_API_KEY"
    assert "secret-value" not in str(provider)
    assert first.active is True
    assert second.active is False
    service.activate_prompt(second.id, "tenant-a")
    runtime = service.resolve_runtime("tenant-a")
    assert runtime.api_key == "secret-value"
    assert runtime.prompt_version == "sql_generation-v2"
    assert "使用业务字段说明" in runtime.system_prompt
    assert SQL_SAFETY_PROMPT in runtime.system_prompt
    assert len(service.list_providers("tenant-a")) == 1
    assert len(service.list_providers("tenant-b")) == 1


def test_provider_validation_rejects_inline_credentials_and_duplicate_names():
    service = ModelOperationsService(InMemoryModelOperationsRepository())
    service.create_provider(tenant_id="tenant-a", **provider_payload())
    try:
        service.create_provider(tenant_id="tenant-a", **provider_payload(name="deepseek primary"))
        raise AssertionError("duplicate provider must be rejected")
    except ValueError as exc:
        assert "E_MODEL_DUPLICATE" in str(exc)
    try:
        service.create_provider(tenant_id="tenant-a", **provider_payload(name="bad", base_url="https://user:secret@example.com"))
        raise AssertionError("URL credentials must be rejected")
    except ValueError as exc:
        assert "E_MODEL_URL" in str(exc)


def test_health_probe_uses_resolved_secret_without_persisting_it():
    observed = {}

    async def probe(provider, api_key):
        observed.update(provider_id=provider.id, api_key=api_key)
        return 23

    repository = InMemoryModelOperationsRepository()
    service = ModelOperationsService(repository, health_probe=probe, environ={"TENANT_MODEL_API_KEY": "health-secret"})
    provider = service.create_provider(tenant_id="tenant-a", **provider_payload())
    checked = asyncio.run(service.check_health(provider.id, "tenant-a"))

    assert observed == {"provider_id": provider.id, "api_key": "health-secret"}
    assert checked.health_status == "healthy"
    assert checked.last_latency_ms == 23
    assert "health-secret" not in str(repository.providers)


def test_missing_secret_is_reported_without_network_probe():
    async def should_not_run(*_args):
        raise AssertionError("probe should not run")

    service = ModelOperationsService(InMemoryModelOperationsRepository(), health_probe=should_not_run, environ={})
    provider = service.create_provider(tenant_id="tenant-a", **provider_payload())
    checked = asyncio.run(service.check_health(provider.id, "tenant-a"))
    assert checked.health_status == "unconfigured"
    assert checked.last_error_code == "E_MODEL_SECRET_MISSING"


def test_default_switch_is_exclusive_and_connection_change_resets_health():
    async def probe(*_args):
        return 8

    service = ModelOperationsService(InMemoryModelOperationsRepository(), health_probe=probe, environ={"TENANT_MODEL_API_KEY": "configured", "SECOND_MODEL_KEY": "configured"})
    first = service.create_provider(tenant_id="tenant-a", **provider_payload())
    second = service.create_provider(tenant_id="tenant-a", **provider_payload(name="Second", api_key_env="SECOND_MODEL_KEY", is_default=False))
    second = service.update_provider(second.id, "tenant-a", {"is_default": True})
    providers = service.list_providers("tenant-a")
    assert [item.id for item in providers if item.is_default] == [second.id]
    assert next(item for item in providers if item.id == first.id).is_default is False

    checked = asyncio.run(service.check_health(second.id, "tenant-a"))
    assert checked.health_status == "healthy"
    changed = service.update_provider(second.id, "tenant-a", {"model": "second-model-v2"})
    assert changed.health_status == "unchecked"
    assert changed.last_checked_at is None
