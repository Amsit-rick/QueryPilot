"""Use-case boundary for safe query execution.

The service depends on injected callables so it can be tested without FastAPI,
LangChain, LangGraph, or a live database.
"""
from collections.abc import Callable
from typing import Any


class QueryExecutionService:
    def __init__(self, *, validate: Callable[[str], str], execute: Callable[[str, dict[str, Any]], Any], apply_policy: Callable[[str, dict], str] | None = None):
        self._validate = validate
        self._execute = execute
        self._apply_policy = apply_policy

    def validate_candidate(self, sql: str, identity: dict | None = None) -> str:
        """Validate SQL, then apply the injected policy boundary."""
        candidate = self._validate(sql)
        return self._apply_policy(candidate, identity or {}) if self._apply_policy else candidate

    def run(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        if not sql or not sql.strip():
            raise ValueError("E_QUERY_EMPTY:查询语句不能为空")
        candidate = self.validate_candidate(sql)
        return self._execute(candidate, params or {})

    def execute_validated(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a candidate after the caller's graph validation step."""
        return self._execute(sql, params or {})
