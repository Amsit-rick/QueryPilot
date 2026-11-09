import pytest
from pydantic import ValidationError

from app.domain.chart_spec import COLUMN, ChartSpec, parse_chart_spec
from app.services.chart_artifact_service import build_chart_artifact_definition
from app.services.result_preview_service import build_result_preview


def test_chart_spec_rejects_renderer_options_scripts_and_remote_resources():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ChartSpec.model_validate({
            "kind": "bar",
            "x_column": "region",
            "y_columns": ["amount"],
            "formatter": "javascript:alert(1)",
            "remote_url": "https://example.invalid/chart.js",
        })


def test_chart_spec_rejects_unknown_or_duplicate_axis_fields():
    with pytest.raises(ValueError, match="E_CHART_FIELD"):
        parse_chart_spec(
            {"kind": "bar", "x_column": "region", "y_columns": ["secret"]},
            columns=["region", "amount"],
        )
    with pytest.raises(ValidationError, match="distinct columns"):
        ChartSpec(kind="bar", x_column="amount", y_columns=("amount",))


def test_chart_spec_accepts_column_and_rejects_removed_rose_kind():
    assert ChartSpec(kind=COLUMN, x_column="region", y_columns=("amount",)).kind == COLUMN
    with pytest.raises(ValidationError, match="Input should be"):
        ChartSpec.model_validate({"kind": "rose", "x_column": "region", "y_columns": ["amount"]})


def test_chart_artifact_contains_only_spec_and_source_reference():
    preview = build_result_preview(
        ["region", "amount"],
        [["华东", 10], ["华南", 20], ["华北", 30]],
    )
    artifact = build_chart_artifact_definition(
        preview,
        source_idempotency_key="task:table:result",
        chart_idempotency_key="task:table:result:chart",
    )

    assert artifact is not None
    assert artifact["kind"] == "chart"
    assert artifact["metadata"]["spec"]["kind"] == "pie"
    assert artifact["metadata"]["source_artifact_idempotency_key"] == "task:table:result"
    assert "rows" not in artifact["metadata"]
    assert "url" not in str(artifact["metadata"]).lower()


def test_unsuitable_result_does_not_create_chart_artifact():
    preview = build_result_preview(["description"], [["only one row"]])
    assert build_chart_artifact_definition(
        preview,
        source_idempotency_key="task:table:result",
        chart_idempotency_key="task:table:result:chart",
    ) is None
