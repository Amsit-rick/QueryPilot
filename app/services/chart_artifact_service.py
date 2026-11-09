"""Create safe chart artifact definitions from redacted table previews."""

from typing import Any

from app.domain.chart_spec import TABLE, parse_chart_spec
from app.services.chart_recommendation_service import recommend_chart


def build_chart_artifact_definition(
    preview: dict[str, Any],
    *,
    source_idempotency_key: str,
    chart_idempotency_key: str,
) -> dict[str, Any] | None:
    """Return a chart artifact definition without copying preview rows."""
    columns = [str(column) for column in (preview.get("columns") or [])]
    recommendation = recommend_chart(preview)
    if recommendation.kind == TABLE:
        return None
    spec = parse_chart_spec(recommendation, columns=columns)
    return {
        "kind": "chart",
        "name": "查询图表配置",
        "idempotency_key": chart_idempotency_key,
        "metadata": {
            "spec": spec.to_dict(),
            "source_artifact_idempotency_key": source_idempotency_key,
            "data_scope": "redacted_reference",
        },
    }


__all__ = ["build_chart_artifact_definition"]
