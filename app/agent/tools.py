"""Small, deterministic tools used by the future LangGraph nodes."""
from collections.abc import Callable

def make_query_tools(*, schema: dict, validate: Callable, execute: Callable) -> dict[str, Callable]:
    return {
        "get_schema": lambda: schema,
        "validate_sql": validate,
        "execute_readonly_query": execute,
    }
