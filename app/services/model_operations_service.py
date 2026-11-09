from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.domain.model_operations import ModelProvider, PromptTemplate, RuntimeModelConfig
from app.model_defaults import DEFAULT_PROMPT_VERSION, compose_system_prompt

_ENV_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


async def probe_openai_compatible(provider: ModelProvider, api_key: str) -> int:
    started = perf_counter()
    async with httpx.AsyncClient(base_url=provider.base_url.rstrip("/"), timeout=settings.model_timeout_seconds) as client:
        response = await client.get("/models", headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
    return max(0, int((perf_counter() - started) * 1000))


class ModelOperationsService:
    def __init__(self, repository, *, health_probe=probe_openai_compatible, environ=None):
        self.repository = repository
        self._health_probe = health_probe
        self._environ = environ if environ is not None else os.environ

    @staticmethod
    def _validate_provider(data):
        if not str(data["name"]).strip() or len(str(data["name"])) > 120:
            raise ValueError("E_MODEL_NAME:提供商名称长度必须为 1 到 120")
        if data["adapter"] not in {"direct", "langchain"}:
            raise ValueError("E_MODEL_ADAPTER:适配器必须为 direct 或 langchain")
        parsed = urlparse(str(data["base_url"]).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("E_MODEL_URL:模型地址必须是无凭据、查询参数和片段的 HTTP(S) 地址")
        if not str(data["model"]).strip() or len(str(data["model"])) > 160:
            raise ValueError("E_MODEL_ID:模型标识长度必须为 1 到 160")
        if not _ENV_REFERENCE.fullmatch(str(data["api_key_env"]).strip()):
            raise ValueError("E_MODEL_SECRET_REF:密钥引用必须是大写环境变量名")

    def list_providers(self, tenant_id):
        return self.repository.list_providers(tenant_id)

    def create_provider(self, *, tenant_id, **data):
        self._validate_provider(data)
        existing = self.repository.list_providers(tenant_id)
        if any(item.name.lower() == data["name"].strip().lower() for item in existing):
            raise ValueError("E_MODEL_DUPLICATE:提供商名称已存在")
        enabled = bool(data.get("enabled", True))
        is_default = enabled and (bool(data.get("is_default")) or not existing)
        provider = ModelProvider(tenant_id=tenant_id, name=data["name"].strip(), adapter=data["adapter"], base_url=data["base_url"].strip().rstrip("/"), model=data["model"].strip(), api_key_env=data["api_key_env"].strip(), enabled=enabled, is_default=is_default)
        return self.repository.create_provider(provider)

    def update_provider(self, provider_id, tenant_id, changes):
        current = self.repository.get_provider(provider_id, tenant_id)
        if not current:
            raise LookupError("模型提供商不存在")
        data = {"name": current.name, "adapter": current.adapter, "base_url": current.base_url, "model": current.model, "api_key_env": current.api_key_env, "enabled": current.enabled, "is_default": current.is_default, **changes}
        self._validate_provider(data)
        if any(item.id != provider_id and item.name.lower() == data["name"].strip().lower() for item in self.repository.list_providers(tenant_id)):
            raise ValueError("E_MODEL_DUPLICATE:提供商名称已存在")
        connection_changed = any(data[field] != getattr(current, field) for field in ("adapter", "base_url", "model", "api_key_env"))
        updated = replace(current, name=data["name"].strip(), adapter=data["adapter"], base_url=data["base_url"].strip().rstrip("/"), model=data["model"].strip(), api_key_env=data["api_key_env"].strip(), enabled=bool(data["enabled"]), is_default=bool(data["is_default"]), health_status="unchecked" if connection_changed else current.health_status, last_latency_ms=0 if connection_changed else current.last_latency_ms, last_error_code="" if connection_changed else current.last_error_code, last_checked_at=None if connection_changed else current.last_checked_at, updated_at=datetime.now(timezone.utc))
        if not updated.enabled:
            updated = replace(updated, is_default=False)
        return self.repository.update_provider(updated)

    async def check_health(self, provider_id, tenant_id):
        current = self.repository.get_provider(provider_id, tenant_id)
        if not current:
            raise LookupError("模型提供商不存在")
        checked_at = datetime.now(timezone.utc)
        api_key = self._environ.get(current.api_key_env, "")
        if not current.enabled:
            updated = replace(current, health_status="disabled", last_error_code="E_MODEL_DISABLED", last_latency_ms=0, last_checked_at=checked_at, updated_at=checked_at)
        elif not api_key:
            updated = replace(current, health_status="unconfigured", last_error_code="E_MODEL_SECRET_MISSING", last_latency_ms=0, last_checked_at=checked_at, updated_at=checked_at)
        else:
            try:
                latency = await self._health_probe(current, api_key)
                updated = replace(current, health_status="healthy", last_error_code="", last_latency_ms=max(0, int(latency)), last_checked_at=checked_at, updated_at=checked_at)
            except Exception:
                updated = replace(current, health_status="unhealthy", last_error_code="E_MODEL_PROBE_FAILED", last_latency_ms=0, last_checked_at=checked_at, updated_at=checked_at)
        return self.repository.update_provider(updated)

    def list_prompts(self, tenant_id, name=""):
        return self.repository.list_prompts(tenant_id, name)

    def create_prompt(self, *, tenant_id, owner_id, name, system_prompt, activate=False):
        normalized_name = str(name).strip()
        prompt_text = str(system_prompt).strip()
        if not _NAME.fullmatch(normalized_name):
            raise ValueError("E_PROMPT_NAME:模板名称只允许字母、数字、点、下划线和连字符")
        if not prompt_text or len(prompt_text) > 8000:
            raise ValueError("E_PROMPT_CONTENT:提示词长度必须为 1 到 8000")
        version = self.repository.next_prompt_version(tenant_id, normalized_name)
        should_activate = bool(activate) or not self.repository.list_prompts(tenant_id, normalized_name)
        return self.repository.create_prompt(PromptTemplate(tenant_id=tenant_id, name=normalized_name, system_prompt=prompt_text, version=version, owner_id=owner_id, active=should_activate))

    def activate_prompt(self, prompt_id, tenant_id):
        prompt = self.repository.activate_prompt(prompt_id, tenant_id)
        if not prompt:
            raise LookupError("提示词版本不存在")
        return prompt

    def resolve_runtime(self, tenant_id):
        provider = next((item for item in self.repository.list_providers(tenant_id) if item.enabled and item.is_default), None)
        prompts = self.repository.list_prompts(tenant_id, "sql_generation")
        prompt = next((item for item in prompts if item.active), None)
        if provider:
            api_key = self._environ.get(provider.api_key_env, "")
            provider_id, provider_name, adapter, base_url, model, source = provider.id, provider.name, provider.adapter, provider.base_url, provider.model, "tenant"
        else:
            api_key = settings.model_api_key
            provider_id, provider_name, adapter, base_url, model, source = "environment", "环境默认配置", settings.model_provider, settings.model_base_url, settings.model_name, "environment"
        return RuntimeModelConfig(provider_id=provider_id, provider_name=provider_name, adapter=adapter, base_url=base_url, model=model, api_key=api_key, prompt_id=prompt.id if prompt else "builtin", prompt_version=f"{prompt.name}-v{prompt.version}" if prompt else DEFAULT_PROMPT_VERSION, system_prompt=compose_system_prompt(prompt.system_prompt if prompt else ""), source=source)
