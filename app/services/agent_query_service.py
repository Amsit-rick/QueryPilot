"""Application service that selects and invokes the configured model chain."""
from collections.abc import Callable
from typing import Any


class AgentQueryService:
    """Keep model-provider selection outside HTTP handlers."""

    def __init__(self, *, provider: str, direct_generate: Callable[..., Any], langchain_generate: Callable[..., Any] | None = None, build_langchain: Callable[..., Any] | None = None, api_key: str = "", base_url: str = "", model: str = "", timeout: float = 30.0, system_prompt: str = "", prompt_version: str = ""):
        self.provider = provider
        self._direct_generate = direct_generate
        self._langchain_generate = langchain_generate
        self._build_langchain = build_langchain
        self._config = {"api_key": api_key, "base_url": base_url, "model": model, "timeout": timeout, "system_prompt": system_prompt, "prompt_version": prompt_version}

    async def generate(self, *, question: str, schema: dict[str, set[str]], context: dict | None, dialect: str, direct_kwargs: dict[str, Any]) -> Any:
        if self.provider == "langchain":
            if not self._langchain_generate or not self._build_langchain:
                raise RuntimeError("LangChain 模型适配器未配置")
            generator = self._build_langchain(**self._config)
            return await self._langchain_generate(generator=generator, question=question, schema=schema, context=context, dialect=dialect, usage_callback=direct_kwargs.get("usage_callback"))
        return await self._direct_generate(question, schema, **direct_kwargs, context=context, dialect=dialect)
