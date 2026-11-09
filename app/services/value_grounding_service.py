"""Bounded grounding of business terms to stored column values.

Business users say "已支付" while the column stores "paid". The model then binds
the Chinese word as a parameter value and the query returns nothing. This
service samples a small, administrator-configured set of enumeration columns so
the generator sees the real stored values.

Safety rules:
- only columns explicitly listed in the datasource ``value_columns`` config;
- the table must already be in the caller's authorized schema;
- identifiers are validated, values are bounded and never cached across users
  in a way that bypasses row-level policy (the caller passes the authorized
  schema; row-scoped columns should not be configured);
- failures degrade to no grounding instead of failing the query.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

Sampler = Callable[[str, str, int], list[Any]]


def parse_value_columns(configured: list[str] | None) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in configured or []:
        if not isinstance(item, str):
            continue
        table, _, column = item.partition(".")
        table = table.strip()
        column = column.strip()
        if not _IDENTIFIER.match(table) or not _IDENTIFIER.match(column):
            continue
        key = (table.lower(), column.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append((table, column))
    return result


class ValueGroundingService:
    def __init__(
        self,
        sampler: Sampler,
        *,
        max_columns: int = 12,
        max_values: int = 16,
        max_value_chars: int = 64,
        ttl_seconds: int = 300,
        clock: Callable[[], float] | None = None,
    ):
        self.sampler = sampler
        self.max_columns = max(1, max_columns)
        self.max_values = max(1, max_values)
        self.max_value_chars = max(8, max_value_chars)
        self.ttl_seconds = max(0, ttl_seconds)
        self.clock = clock or time.monotonic
        self._cache: dict[tuple[str, str, str], tuple[float, list[str]]] = {}

    def _values(self, datasource_id: str, table: str, column: str) -> list[str]:
        key = (datasource_id, table.lower(), column.lower())
        now = self.clock()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        try:
            raw = self.sampler(table, column, self.max_values)
        except Exception:
            return []
        values: list[str] = []
        for value in raw or []:
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            if len(text) > self.max_value_chars:
                text = text[: self.max_value_chars]
            if text not in values:
                values.append(text)
            if len(values) >= self.max_values:
                break
        self._cache[key] = (now + self.ttl_seconds, values)
        return values

    def context(
        self,
        *,
        datasource_id: str,
        allowed_tables: set[str] | list[str],
        configured: list[str] | None,
    ) -> dict[str, dict[str, list[str]]]:
        allowed = {str(table).lower() for table in allowed_tables}
        pairs = parse_value_columns(configured)
        if not pairs:
            return {}
        result: dict[str, dict[str, list[str]]] = {}
        used = 0
        for table, column in pairs:
            if used >= self.max_columns:
                break
            if table.lower() not in allowed:
                continue
            values = self._values(datasource_id, table, column)
            if not values:
                continue
            result.setdefault(table, {})[column] = values
            used += 1
        return result

    def clear(self) -> None:
        self._cache.clear()
