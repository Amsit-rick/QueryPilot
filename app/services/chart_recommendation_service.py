"""Deterministic, explainable chart recommendations for result previews.

Deliberately conservative: only a small fixed set of basic charts is proposed.
There is no automatic multi-axis layout, no combined chart synthesis and no
model-driven chart design. When the data shape is unclear the recommendation
falls back to a table, which is always correct.

Input is the normalized preview produced by RA-01, so numeric columns arrive as
strings (Decimal is preserved as text) and timestamps as ISO strings.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.chart_spec import BAR, COLUMN, LINE, PIE, SCATTER, TABLE, ChartSpec
from app.services.result_preview_service import NOTE_DECIMAL, NOTE_TEMPORAL

SUPPORTED_CHARTS: frozenset[str] = frozenset({BAR, COLUMN, LINE, PIE, SCATTER, TABLE})

NUMERIC = "numeric"
TEMPORAL = "temporal"
CATEGORICAL = "categorical"
UNKNOWN = "unknown"

# Above this many distinct values a column stops being a useful axis category.
MAX_CATEGORY_CARDINALITY = 20
MAX_PIE_SLICES = 8
MIN_ROWS_FOR_CHART = 2

_ISO_DATE_PREFIX_LENGTH = 10


ChartRecommendation = ChartSpec


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        try:
            return value == value and abs(float(value)) != float("inf")
        except (TypeError, ValueError, OverflowError):
            return False
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return False
        return not parsed.is_nan() and not parsed.is_infinite()
    return False


def _is_temporal(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < _ISO_DATE_PREFIX_LENGTH:
        return False
    head = value[:_ISO_DATE_PREFIX_LENGTH]
    return (
        len(head) == _ISO_DATE_PREFIX_LENGTH
        and head[4] == "-"
        and head[7] == "-"
        and head[:4].isdigit()
        and head[5:7].isdigit()
        and head[8:10].isdigit()
    )


def infer_column_types(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    notes: dict[str, list[str]] | None = None,
) -> list[str]:
    """Classify each column from its values, with RA-01 notes as a shortcut."""
    notes = notes or {}
    types: list[str] = []

    for index, column in enumerate(columns):
        column_notes = notes.get(str(column), [])
        values = [row[index] for row in rows if index < len(row)]
        present = [value for value in values if value is not None]

        if not present:
            types.append(UNKNOWN)
            continue

        if all(_is_temporal(value) for value in present):
            types.append(TEMPORAL)
            continue

        # A column normalized from Decimal is numeric even though it is a string.
        if NOTE_DECIMAL in column_notes or all(_is_number(value) for value in present):
            types.append(NUMERIC)
            continue

        if NOTE_TEMPORAL in column_notes:
            types.append(TEMPORAL)
            continue

        distinct = len({str(value) for value in present})
        if distinct <= MAX_CATEGORY_CARDINALITY:
            types.append(CATEGORICAL)
        else:
            types.append(UNKNOWN)

    return types


def recommend_chart(preview: dict[str, Any]) -> ChartRecommendation:
    """Suggest one basic chart for a normalized result preview."""
    columns = list(preview.get("columns") or [])
    rows = list(preview.get("rows") or [])
    notes = preview.get("normalized_notes") or {}

    fallback_table = ChartSpec(
        kind=TABLE,
        x_column="",
        y_columns=(),
        reason="结果不适合自动图表，使用表格展示最稳妥",
        column_types=tuple(infer_column_types(columns, rows, notes)),
    )

    if not columns or len(rows) < MIN_ROWS_FOR_CHART:
        return fallback_table

    types = infer_column_types(columns, rows, notes)
    numeric_positions = [index for index, kind in enumerate(types) if kind == NUMERIC]
    temporal_positions = [index for index, kind in enumerate(types) if kind == TEMPORAL]
    category_positions = [index for index, kind in enumerate(types) if kind == CATEGORICAL]

    if temporal_positions and numeric_positions:
        x = temporal_positions[0]
        y = [index for index in numeric_positions if index != x]
        if y:
            return ChartSpec(
                kind=LINE,
                x_column=columns[x],
                y_columns=tuple(columns[index] for index in y[:1]),
                reason="首列为时间且存在数值列，适合按时间趋势展示",
                column_types=tuple(types),
            )

    if category_positions and numeric_positions:
        x = category_positions[0]
        y = [index for index in numeric_positions if index != x]
        if not y:
            return fallback_table
        distinct = len({str(row[x]) for row in rows if x < len(row) and row[x] is not None})
        if distinct <= MAX_PIE_SLICES and len(rows) <= MAX_PIE_SLICES:
            return ChartSpec(
                kind=PIE,
                x_column=columns[x],
                y_columns=(columns[y[0]],),
                reason=f"分类数仅 {distinct} 个且行数较少，适合展示占比",
                column_types=tuple(types),
            )
        return ChartSpec(
            kind=BAR,
            x_column=columns[x],
            y_columns=tuple(columns[index] for index in y[:1]),
            reason="存在分类列与数值列，适合用条形图比较各类别数值",
            column_types=tuple(types),
        )

    if len(numeric_positions) >= 2:
        x, y = numeric_positions[0], numeric_positions[1]
        return ChartSpec(
            kind=SCATTER,
            x_column=columns[x],
            y_columns=(columns[y],),
            reason="存在两个数值列，适合用散点图观察相关性",
            column_types=tuple(types),
        )

    return fallback_table
