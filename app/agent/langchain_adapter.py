"""Optional LangChain model adapter for DeepSeek's OpenAI-compatible API."""
from collections.abc import Callable
from typing import Any
from .contracts import SqlPlan, parse_sql_plan
from app.model_defaults import DEFAULT_PROMPT_VERSION, compose_system_prompt


def build_sql_generator(*, api_key: str, base_url: str, model: str, timeout: float, system_prompt: str = "", prompt_version: str = DEFAULT_PROMPT_VERSION):
    try:
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.messages import SystemMessage
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError("MODEL_PROVIDER=langchain 需要安装 langchain-openai") from exc

    effective_prompt = system_prompt or compose_system_prompt()
    prompt = ChatPromptTemplate.from_messages([
        SystemMessage(content=f"Prompt版本：{prompt_version}。{effective_prompt} 返回 JSON 对象，字段必须为 sql、params、intent、confidence、needs_approval。SQL 中的用户筛选值必须写成 :name 命名参数，并把对应的标量值放入 params；不得把用户值拼接进 SQL。"),
        ("human", "目标数据库方言：{dialect}\nSchema:\n{schema}\n检索上下文：{context}\n问题：{question}"),
    ])
    llm = ChatOpenAI(api_key=api_key, base_url=base_url.rstrip("/"), model=model, temperature=0, timeout=timeout, max_retries=0)
    try:
        return prompt | llm.with_structured_output(SqlPlan)
    except (AttributeError, TypeError, NotImplementedError):
        return prompt | llm | StrOutputParser()


def _extract_usage(value: Any) -> dict[str, int]:
    usage = getattr(value, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(value, "response_metadata", None)
        usage = metadata.get("token_usage", {}) if isinstance(metadata, dict) else {}
    if not isinstance(usage, dict):
        return {}
    if not any(name in usage for name in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens")):
        return {}
    def count(*names: str) -> int:
        for name in names:
            if name not in usage:
                continue
            try:
                return max(0, int(usage.get(name, 0) or 0))
            except (TypeError, ValueError, OverflowError):
                continue
        return 0
    details = usage.get("input_token_details") if isinstance(usage.get("input_token_details"), dict) else {}
    return {
        "input_tokens": count("input_tokens", "prompt_tokens"),
        "output_tokens": count("output_tokens", "completion_tokens"),
        "cache_hit_input_tokens": count("prompt_cache_hit_tokens", "cache_read_input_tokens") or _usage_detail(details, "cache_read", "cache_hit"),
        "cache_miss_input_tokens": count("prompt_cache_miss_tokens", "cache_creation_input_tokens") or _usage_detail(details, "cache_miss", "cache_creation"),
    }


def _usage_detail(details: dict[str, Any], *names: str) -> int:
    for name in names:
        try:
            return max(0, int(details.get(name, 0) or 0))
        except (TypeError, ValueError, OverflowError):
            continue
    return 0


async def generate_sql(*, generator: Any, question: str, schema: dict[str, set[str]], context: dict | None, dialect: str, usage_callback: Callable[[dict[str, int]], None] | None = None) -> SqlPlan:
    schema_text = "\n".join(f"{table}({', '.join(sorted(columns))})" for table, columns in schema.items())
    sql = await generator.ainvoke({"dialect": dialect, "schema": schema_text, "context": context or {}, "question": question})
    usage = _extract_usage(sql)
    if usage_callback and usage:
        usage_callback(usage)
    return parse_sql_plan(getattr(sql, "content", sql))
