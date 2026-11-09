"""Small process-local cache for deterministic SQL generation."""
from time import monotonic
from typing import Any

_entries: dict[str, tuple[float, Any]] = {}


def get(key: str) -> Any | None:
    item = _entries.get(key)
    if not item:
        return None
    expires, value = item
    if expires <= monotonic():
        _entries.pop(key, None)
        return None
    return value


def put(key: str, value: Any, ttl: int, max_entries: int) -> None:
    if ttl <= 0:
        return
    while len(_entries) >= max(1, max_entries):
        _entries.pop(next(iter(_entries)))
    _entries[key] = (monotonic() + ttl, value)


def clear() -> None:
    _entries.clear()
