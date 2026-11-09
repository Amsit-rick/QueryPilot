"""Runtime wiring for column-value grounding."""

from __future__ import annotations

from .config import settings
from .services.value_grounding_service import ValueGroundingService

_SERVICES: dict[str, ValueGroundingService] = {}


def _mysql_sampler(table: str, column: str, limit: int) -> list:
    from .mysql_db import distinct_values

    return distinct_values(table, column, limit)


def get_value_grounding_service(backend: str | None) -> ValueGroundingService | None:
    """Return a sampler for backends with an implemented distinct-value query.

    SQLite, PostgreSQL and the remote experimental gateways are intentionally
    left out for now: the feature only activates for administrators who list
    columns in the datasource ``value_columns`` config.
    """
    name = str(backend or "").lower()
    if name != "mysql":
        return None
    service = _SERVICES.get(name)
    if service is None:
        service = ValueGroundingService(
            _mysql_sampler,
            max_columns=settings.value_grounding_max_columns,
            max_values=settings.value_grounding_max_values,
            max_value_chars=settings.value_grounding_max_value_chars,
            ttl_seconds=settings.value_grounding_ttl_seconds,
        )
        _SERVICES[name] = service
    return service


def reset_value_grounding_for_tests() -> None:
    _SERVICES.clear()
