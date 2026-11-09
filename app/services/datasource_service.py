"""Application service for resolving and accessing authorized data sources."""
from collections.abc import Callable
from typing import Any


class DataSourceAccessService:
    """Keep datasource lookup and role checks out of HTTP route handlers."""

    def __init__(
        self,
        *,
        get_datasource: Callable[[str], dict | None],
        can_use: Callable[[dict, dict], bool],
        schema: Callable[[dict], dict[str, set[str]]],
        execute: Callable[[dict, str, dict[str, Any], int, int], Any],
    ):
        self._get_datasource = get_datasource
        self._can_use = can_use
        self._schema = schema
        self._execute = execute

    def resolve(self, identity: dict, datasource_id: str) -> dict:
        datasource = self._get_datasource(datasource_id)
        if not datasource:
            raise LookupError("E_DATASOURCE_NOT_FOUND:数据源不存在")
        if not self._can_use(identity, datasource):
            raise PermissionError("E_DATASOURCE_FORBIDDEN:无权访问该数据源")
        return datasource

    def load_schema(self, identity: dict, datasource_id: str) -> tuple[dict, dict[str, set[str]]]:
        datasource = self.resolve(identity, datasource_id)
        return datasource, self._schema(datasource)

    def execute(self, identity: dict, datasource_id: str, sql: str, params: dict[str, Any], max_rows: int, offset: int = 0):
        datasource = self.resolve(identity, datasource_id)
        return self._execute(datasource, sql, params, max_rows, offset)
