"""Resolve database and manually curated relationships into safe model context."""
from __future__ import annotations

from typing import Any


def _normalise(item: dict[str, Any], *, source_default: str = "manual") -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    required = ("from_table", "from_columns", "to_table", "to_columns")
    if any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("from_table", "to_table")):
        return None
    if not all(isinstance(item.get(key), list) and item[key] and all(isinstance(value, str) and value.strip() for value in item[key]) for key in ("from_columns", "to_columns")):
        return None
    if len(item["from_columns"]) != len(item["to_columns"]):
        return None
    confidence = item.get("confidence", 0.8 if source_default == "manual" else 1.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return None
    if not 0 <= confidence <= 1:
        return None
    source = item.get("source", source_default)
    if not isinstance(source, str) or not source.strip() or len(source) > 64:
        return None
    return {
        "from_table": item["from_table"],
        "from_columns": list(item["from_columns"]),
        "to_table": item["to_table"],
        "to_columns": list(item["to_columns"]),
        "cardinality": item.get("cardinality", "unknown") if item.get("cardinality", "unknown") in {"one_to_one", "one_to_many", "many_to_one", "many_to_many", "unknown"} else "unknown",
        "confidence": confidence,
        "source": source,
        "version": str(item.get("version", "manual-v1"))[:64],
        "approved": True if source_default == "database_metadata" else bool(item.get("approved", False)),
    }


def resolve_relationships(schema: dict[str, list[str] | set[str]], *, database: list[dict[str, Any]] | None = None, manual: list[dict[str, Any]] | None = None, datasource_id: str | None = None, tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Merge validated relationships and drop anything outside the visible schema."""
    visible = {str(table).lower(): {str(column).lower() for column in columns} for table, columns in schema.items()}
    output: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    candidates = [(raw, "database_metadata") for raw in (database or [])]
    candidates.extend((raw, "manual") for raw in (manual or []))
    for raw, source in candidates:
        if source == "manual":
            if raw.get("datasource_id") not in (None, "", datasource_id):
                continue
            if raw.get("tenant_id") not in (None, "", tenant_id):
                continue
        item = _normalise(raw, source_default=source)
        if not item:
            continue
        from_key, to_key = item["from_table"].lower(), item["to_table"].lower()
        if from_key not in visible or to_key not in visible:
            continue
        if any(column.lower() not in visible[from_key] for column in item["from_columns"]):
            continue
        if any(column.lower() not in visible[to_key] for column in item["to_columns"]):
            continue
        key = (from_key, tuple(column.lower() for column in item["from_columns"]), to_key, tuple(column.lower() for column in item["to_columns"]))
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


__all__ = ["resolve_relationships"]
