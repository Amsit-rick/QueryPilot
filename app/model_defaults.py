"""Non-overridable SQL model defaults shared by all provider adapters."""

DEFAULT_PROMPT_VERSION = "sql-generator-v2"
SQL_SAFETY_PROMPT = (
    "你是 SQL 生成器。只能使用给定 schema 中的表和字段；只生成一条只读 SELECT；"
    "禁止 DDL、DML、事务、文件、网络和多语句操作；不要输出 Markdown。"
)


def compose_system_prompt(custom_prompt: str = "") -> str:
    custom = str(custom_prompt or "").strip()
    return f"{custom}\n\n不可覆盖的安全约束：{SQL_SAFETY_PROMPT}" if custom else SQL_SAFETY_PROMPT
