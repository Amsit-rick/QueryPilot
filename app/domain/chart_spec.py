"""Strict, renderer-independent contract for persisted chart configuration."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BAR = "bar"
COLUMN = "column"
LINE = "line"
PIE = "pie"
SCATTER = "scatter"
TABLE = "table"

ChartKind = Literal["bar", "column", "line", "pie", "scatter", "table"]
ColumnKind = Literal["numeric", "temporal", "categorical", "unknown"]


class ChartSpec(BaseModel):
    """A bounded declarative spec; arbitrary renderer options are forbidden."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    kind: ChartKind
    x_column: str = Field(default="", max_length=128)
    y_columns: tuple[str, ...] = Field(default_factory=tuple, max_length=4)
    reason: str = Field(default="", max_length=500)
    column_types: tuple[ColumnKind, ...] = Field(default_factory=tuple, max_length=100)
    max_points: int = Field(default=100, ge=1, le=200)

    @model_validator(mode="after")
    def validate_axes(self):
        if self.kind == TABLE:
            if self.x_column or self.y_columns:
                raise ValueError("table fallback must not define chart axes")
            return self
        if not self.x_column or not self.y_columns:
            raise ValueError("chart axes must include one x column and at least one y column")
        if self.x_column in self.y_columns or len(set(self.y_columns)) != len(self.y_columns):
            raise ValueError("chart axes must reference distinct columns")
        if self.kind in {PIE, SCATTER} and len(self.y_columns) != 1:
            raise ValueError(f"{self.kind} chart requires exactly one y column")
        return self

    def validate_source_columns(self, columns: list[str] | tuple[str, ...]) -> "ChartSpec":
        allowed = {str(column) for column in columns}
        references = ({self.x_column} | set(self.y_columns)) - {""}
        unknown = references - allowed
        if unknown:
            raise ValueError(f"E_CHART_FIELD:图表引用了查询结果中不存在的字段 {sorted(unknown)}")
        if self.column_types and len(self.column_types) != len(columns):
            raise ValueError("E_CHART_TYPES:图表字段类型数量与查询列不一致")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def parse_chart_spec(value: ChartSpec | dict[str, Any], *, columns: list[str] | tuple[str, ...]) -> ChartSpec:
    spec = value if isinstance(value, ChartSpec) else ChartSpec.model_validate(value)
    return spec.validate_source_columns(columns)


__all__ = ["BAR", "COLUMN", "LINE", "PIE", "SCATTER", "TABLE", "ChartSpec", "parse_chart_spec"]
