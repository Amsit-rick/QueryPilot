"""Deterministic JSON normalization and capacity budget for query result previews.

This module answers one question: given raw database rows, what exactly may be
serialized into a JSON artifact preview without breaking strict JSON parsers,
losing numeric precision, or exceeding a byte budget.

It runs strictly after authorization and redaction. It never reads a database,
never contacts a model, and never decides what a user is allowed to see.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

TRUNCATION_SUFFIX = "…[truncated]"
BINARY_OMITTED_PREFIX = "[binary omitted:"

# Truncation reasons recorded on the preview payload.
REASON_ROW_LIMIT = "row_limit"
REASON_CELL_BUDGET = "cell_budget"
REASON_ROW_BUDGET = "row_budget"
REASON_TOTAL_BUDGET = "total_budget"
REASON_SOURCE_TRUNCATED = "source_truncated"

# Normalization notes describing how a value was transformed.
NOTE_BINARY = "binary"
NOTE_BINARY_BASE64 = "binary_base64"
NOTE_COERCED = "coerced"
NOTE_DECIMAL = "decimal"
NOTE_NON_FINITE = "non_finite"
NOTE_TEMPORAL = "temporal"
NOTE_TRUNCATED = "truncated"

DEFAULT_MAX_ROWS = 100
DEFAULT_MAX_CELL_BYTES = 4096
DEFAULT_MAX_ROW_BYTES = 65536
DEFAULT_MAX_TOTAL_BYTES = 1048576
DEFAULT_BASE64_LIMIT_BYTES = 256


@dataclass(frozen=True)
class PreviewBudget:
    """Byte and row ceilings applied while building a preview."""

    max_rows: int = DEFAULT_MAX_ROWS
    max_cell_bytes: int = DEFAULT_MAX_CELL_BYTES
    max_row_bytes: int = DEFAULT_MAX_ROW_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    # "omit" never emits binary content; "base64" emits it below the size limit.
    bytes_policy: str = "omit"
    base64_limit_bytes: int = DEFAULT_BASE64_LIMIT_BYTES

    def __post_init__(self) -> None:
        if self.bytes_policy not in {"omit", "base64"}:
            raise ValueError(f"bytes_policy must be 'omit' or 'base64', got {self.bytes_policy!r}")
        for name in ("max_rows", "max_cell_bytes", "max_row_bytes", "max_total_bytes", "base64_limit_bytes"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


def byte_length(value: Any) -> int:
    """Approximate UTF-8 size of a value once serialized to JSON text."""
    if value is None:
        return 4
    if isinstance(value, bool):
        return 5 if value else 4
    if isinstance(value, int):
        return len(str(value))
    if isinstance(value, float):
        return len(repr(value))
    if isinstance(value, str):
        return len(value.encode("utf-8")) + 2
    return len(repr(value).encode("utf-8"))


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Cut text so its UTF-8 encoding fits the budget, preserving characters."""
    if max_bytes <= 0:
        return "", len(text) > 0
    if len(text.encode("utf-8")) <= max_bytes:
        return text, False

    suffix = TRUNCATION_SUFFIX
    budget = max_bytes - len(suffix.encode("utf-8"))
    if budget <= 0:
        return "", True

    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(text[:mid].encode("utf-8")) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low] + suffix, True


def _is_non_finite(value: Any) -> bool:
    """Detect NaN and Infinity across float and Decimal without raising."""
    if isinstance(value, float):
        return value != value or value in (float("inf"), float("-inf"))
    if isinstance(value, Decimal):
        try:
            return value.is_nan() or value.is_infinite()
        except (InvalidOperation, AttributeError):
            return False
    return False


def normalize_value(value: Any, budget: PreviewBudget = PreviewBudget()) -> tuple[Any, str]:
    """Convert one database value into a JSON-safe value plus a normalization note.

    Returns a tuple of ``(safe_value, note)`` where note is an empty string when
    the value needed no transformation.
    """
    if value is None:
        return None, ""

    if isinstance(value, bool):
        return value, ""

    if isinstance(value, str):
        text, was_cut = _truncate_text(value, budget.max_cell_bytes)
        return text, NOTE_TRUNCATED if was_cut else ""

    if isinstance(value, int):
        return value, ""

    if isinstance(value, float):
        # NaN and Infinity are not valid JSON and break strict parsers.
        if _is_non_finite(value):
            return None, NOTE_NON_FINITE
        return value, ""

    if isinstance(value, Decimal):
        if _is_non_finite(value):
            return None, NOTE_NON_FINITE
        # str keeps precision; float would silently lose it.
        return str(value), NOTE_DECIMAL

    if isinstance(value, (datetime, date, time)):
        return value.isoformat(), NOTE_TEMPORAL

    if isinstance(value, timedelta):
        return value.total_seconds(), NOTE_TEMPORAL

    if isinstance(value, UUID):
        return str(value), ""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return _normalize_binary(raw, budget)

    if isinstance(value, (set, frozenset)):
        items = [normalize_value(item, budget)[0] for item in value]
        try:
            items.sort(key=lambda item: (str(type(item)), str(item)))
        except TypeError:
            items = sorted((str(item) for item in items), key=str)
        return items, NOTE_COERCED

    if isinstance(value, (list, tuple)):
        return [normalize_value(item, budget)[0] for item in value], NOTE_COERCED

    if isinstance(value, dict):
        return {str(key): normalize_value(item, budget)[0] for key, item in value.items()}, NOTE_COERCED

    text, was_cut = _truncate_text(str(value), budget.max_cell_bytes)
    if was_cut:
        return text, NOTE_COERCED
    return text, NOTE_COERCED


def _normalize_binary(raw: bytes, budget: PreviewBudget) -> tuple[Any, str]:
    """Never leak binary payloads by default; only emit metadata about them."""
    size = len(raw)
    if budget.bytes_policy == "base64" and size <= budget.base64_limit_bytes:
        import base64

        return base64.b64encode(raw).decode("ascii"), NOTE_BINARY_BASE64
    return f"{BINARY_OMITTED_PREFIX}{size} bytes]", NOTE_BINARY


def build_result_preview(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    *,
    budget: PreviewBudget | None = None,
    source_truncated: bool = False,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a bounded, JSON-safe preview payload for a query result.

    The returned dict keeps the field names used by existing task artifact
    metadata so the integration point can be swapped without schema churn.
    """
    active_budget = budget or PreviewBudget()
    reasons: list[str] = []
    if source_truncated:
        reasons.append(REASON_SOURCE_TRUNCATED)

    total_rows = len(rows)
    preview_rows: list[list[Any]] = []
    column_notes: dict[str, list[str]] = {}
    total_bytes = 0
    skipped_rows = 0
    budget_exhausted = False

    for index, row in enumerate(rows):
        if index >= active_budget.max_rows:
            _append_reason(reasons, REASON_ROW_LIMIT)
            break
        if budget_exhausted:
            break

        cells: list[Any] = []
        row_bytes = 0
        row_capped = False

        for position, cell in enumerate(row):
            value, note = normalize_value(cell, active_budget)
            cell_bytes = byte_length(value)

            if cell_bytes > active_budget.max_cell_bytes:
                _append_reason(reasons, REASON_CELL_BUDGET)

            if row_bytes + cell_bytes > active_budget.max_row_bytes:
                row_capped = True
                _append_reason(reasons, REASON_ROW_BUDGET)
                break

            cells.append(value)
            row_bytes += cell_bytes
            _record_note(column_notes, columns, position, note)

        if row_capped and not cells:
            # Even the first cell exceeds the row budget: skip this row and keep
            # going, but guard against an endless scan of oversized rows.
            skipped_rows += 1
            if skipped_rows > active_budget.max_rows:
                budget_exhausted = True
                break
            continue

        if total_bytes + row_bytes > active_budget.max_total_bytes:
            _append_reason(reasons, REASON_TOTAL_BUDGET)
            budget_exhausted = True
            break

        preview_rows.append(cells)
        total_bytes += row_bytes

    payload: dict[str, Any] = {
        "columns": list(columns),
        "rows": preview_rows,
        "row_count": total_rows,
        "preview_count": len(preview_rows),
        "preview_limit": active_budget.max_rows,
        "truncated": bool(reasons),
        "truncation_reasons": reasons,
        "normalized_notes": {key: sorted(set(notes)) for key, notes in column_notes.items()},
        "byte_size": total_bytes,
        "byte_budget": active_budget.max_total_bytes,
    }
    if extra_metadata:
        payload.update(extra_metadata)
    return payload


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _record_note(store: dict[str, list[str]], columns: Sequence[str], position: int, note: str) -> None:
    if not note:
        return
    name = columns[position] if position < len(columns) else f"column_{position}"
    store.setdefault(str(name), []).append(note)


def strict_json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Last-resort guard: fail loudly rather than emit non-standard JSON.

    Integration points may call this before serialization. It raises instead of
    silently producing NaN or Infinity literals that break strict parsers.
    """
    import json

    def reject(value: Any) -> Any:
        if isinstance(value, float) and _is_non_finite(value):
            raise ValueError("non-finite float reached serialization boundary")
        return value

    json.dumps(payload, default=reject, allow_nan=False)
    return payload


__all__ = [
    "PreviewBudget",
    "build_result_preview",
    "byte_length",
    "normalize_value",
    "strict_json_safe",
]
