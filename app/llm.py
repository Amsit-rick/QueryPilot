import httpx
import hashlib
import json
import time
from collections.abc import Callable
from .sql_cache import get, put
from .config import settings
from .circuit_breaker import allow_request, record_failure, record_success
from .model_defaults import DEFAULT_PROMPT_VERSION, compose_system_prompt
from .agent.contracts import SqlPlan


PROMPT_VERSION = DEFAULT_PROMPT_VERSION
SYSTEM_PROMPT = compose_system_prompt()


class ModelConfigurationError(RuntimeError):
    code = "E_MODEL_CONFIG"
    retryable = False


def _usage_count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


async def generate_sql(question: str, schema: dict[str, set[str]], api_key: str, base_url: str, model: str, timeout: float, context: dict | None = None, dialect: str = "sqlite", cache_ttl: int = 300, cache_max_entries: int = 512, usage_callback: Callable[[dict[str, int]], None] | None = None, system_prompt: str = SYSTEM_PROMPT, prompt_version: str = PROMPT_VERSION) -> str | dict:
    if not api_key:
        raise ModelConfigurationError("未配置 MODEL_API_KEY；请传入 SQL，或在 .env 中配置 DeepSeek/OpenAI API Key 后重启服务")
    schema_text = "\n".join(f"{table}({', '.join(sorted(columns))})" for table, columns in schema.items())
    cache_key = hashlib.sha256(json.dumps([prompt_version, system_prompt, model, dialect, question, schema_text, context or {}], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cached = get(cache_key)
    if cached:
        return cached
    circuit_url = settings.redis_url if settings.llm_circuit_backend == "redis" else None
    circuit_key = f"{base_url.rstrip('/')}/{model}"
    allow_request(
        key=circuit_key,
        threshold=settings.llm_circuit_failures,
        reset_seconds=settings.llm_circuit_reset_seconds,
        redis_url=circuit_url,
    )
    dialect_hint = f"目标数据库方言：{dialect}。使用该数据库支持的日期、分页和字符串函数。"
    contract_hint = "优先返回 JSON 对象，字段为 sql、params、intent、confidence（0 到 1）和 needs_approval。用户筛选值必须使用 :name 命名占位符，并作为标量放入 params，禁止拼接到 SQL；无法结构化时可返回裸 SQL。"
    payload = {"model": model, "temperature": 0, "top_p": 1, "stream": False, "messages": [{"role": "system", "content": f"Prompt版本：{prompt_version}。{system_prompt} {dialect_hint} {contract_hint}"}, {"role": "user", "content": f"Schema:\n{schema_text}\n检索上下文：{context or {}}\n问题：{question}"}]}
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=settings.model_http_trust_env) as client:
        response = await client.post("/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        try:
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"].strip().strip("`").removeprefix("json").strip()
            try:
                structured = json.loads(content)
                generated = SqlPlan.model_validate(structured).model_dump() if isinstance(structured, dict) else content
            except (json.JSONDecodeError, ValueError):
                generated = content.removeprefix("sql").strip()
            usage = body.get("usage") or {}
            if usage_callback and isinstance(usage, dict):
                usage_callback({"input_tokens": _usage_count(usage.get("prompt_tokens", usage.get("input_tokens", 0))), "output_tokens": _usage_count(usage.get("completion_tokens", usage.get("output_tokens", 0)))})
        except Exception:
            record_failure(
                key=circuit_key,
                threshold=settings.llm_circuit_failures,
                reset_seconds=settings.llm_circuit_reset_seconds,
                redis_url=circuit_url,
            )
            raise
        record_success(
            key=circuit_key,
            threshold=settings.llm_circuit_failures,
            reset_seconds=settings.llm_circuit_reset_seconds,
            redis_url=circuit_url,
        )
        # Parameter values can contain business-sensitive filters. Cache only
        # plans without generated values; the key itself is already hashed.
        if not (isinstance(generated, dict) and generated.get("params")):
            put(cache_key, generated, cache_ttl, cache_max_entries)
        return generated
