"""Tests for chart recommendation and safe asset reuse. No database required."""

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.result_asset import AccessContext
from app.repositories.result_asset_protocol import InMemoryResultAssetRepository
from app.services.artifact_reference_service import ArtifactReferenceService
from app.services.chart_recommendation_service import (
    BAR,
    LINE,
    PIE,
    SCATTER,
    TABLE,
    infer_column_types,
    recommend_chart,
)
from app.services.result_asset_service import ResultAssetService
from app.services.result_preview_service import build_result_preview

TENANT = "tenant-a"
OWNER = "user-1"
OTHER_USER = "user-2"
FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def owner_context(roles=()):
    return AccessContext(tenant_id=TENANT, user_id=OWNER, roles=tuple(roles))


def preview_of(columns, rows):
    return build_result_preview(columns, rows)


# --- column type inference ---


def test_decimal_strings_are_recognized_as_numeric():
    types = infer_column_types(["a"], [["1.10"], ["2.50"]])
    assert types == ["numeric"]


def test_iso_strings_are_recognized_as_temporal():
    types = infer_column_types(["d"], [["2026-09-12"], ["2026-09-13"]])
    assert types == ["temporal"]


def test_low_cardinality_text_is_categorical():
    types = infer_column_types(["r"], [["华东"], ["华南"], ["华东"]])
    assert types == ["categorical"]


def test_high_cardinality_text_is_unknown():
    rows = [[f"id-{index}"] for index in range(60)]
    assert infer_column_types(["uid"], rows) == ["unknown"]


def test_all_null_column_is_unknown():
    assert infer_column_types(["x"], [[None], [None]]) == ["unknown"]


# --- chart recommendations ---


def test_temporal_plus_numeric_recommends_line():
    preview = preview_of(
        ["date", "amount"],
        [["2026-09-01", "10"], ["2026-09-02", "20"], ["2026-09-03", "15"]],
    )
    result = recommend_chart(preview)
    assert result.kind == LINE
    assert result.x_column == "date"
    assert "时间" in result.reason


def test_category_plus_numeric_recommends_bar():
    rows = [[f"区域{index}", str(index * 10)] for index in range(12)]
    result = recommend_chart(preview_of(["region", "sales"], rows))
    assert result.kind == BAR
    assert result.x_column == "region"


def test_few_slices_recommends_pie():
    rows = [["A", "10"], ["B", "20"], ["C", "5"]]
    result = recommend_chart(preview_of(["type", "count"], rows))
    assert result.kind == PIE


def test_two_numeric_columns_recommend_scatter():
    rows = [[str(index), str(index * 2)] for index in range(30)]
    result = recommend_chart(preview_of(["x", "y"], rows))
    assert result.kind == SCATTER


def test_empty_result_falls_back_to_table():
    assert recommend_chart(preview_of([], [])).kind == TABLE


def test_single_row_falls_back_to_table():
    result = recommend_chart(preview_of(["a", "b"], [["x", "1"]]))
    assert result.kind == TABLE


def test_no_usable_shape_falls_back_to_table():
    rows = [[f"text-{index}"] for index in range(30)]
    result = recommend_chart(preview_of(["blob"], rows))
    assert result.kind == TABLE


def test_recommendation_is_deterministic():
    rows = [["2026-01-0" + str(day), str(day)] for day in range(1, 6)]
    preview = preview_of(["d", "v"], rows)
    assert recommend_chart(preview).to_dict() == recommend_chart(preview).to_dict()


def test_only_basic_charts_are_ever_returned():
    allowed = {BAR, LINE, PIE, SCATTER, TABLE}
    samples = [
        preview_of(["a", "b"], [["1", "2"], ["3", "4"]]),
        preview_of(["d", "v"], [["2026-01-01", "5"], ["2026-01-02", "6"]]),
        preview_of(["c", "v"], [["x", "1"], ["y", "2"]]),
        preview_of([], []),
    ]
    for preview in samples:
        assert recommend_chart(preview).kind in allowed


# --- safe asset reference ---


def build_stack():
    asset_service = ResultAssetService(
        InMemoryResultAssetRepository(), clock=lambda: FIXED_NOW
    )
    return asset_service, ArtifactReferenceService(asset_service)


def make_table(asset_service, context=None, columns=None, rows=None, **kwargs):
    return asset_service.create_table(
        task_id="t1",
        context=context or owner_context(),
        columns=columns or ["region", "amount"],
        rows=rows or [["华东", "100"], ["华南", "200"], ["华北", "300"]],
        semantic_version="sem-1",
        data_version="sales@2026-09-12",
        **kwargs,
    )


def test_owner_can_reference_own_asset():
    asset_service, references = build_stack()
    asset = make_table(asset_service)
    ref = references.resolve(asset.id, owner_context())
    assert ref.asset_id == asset.id
    assert ref.columns == ("region", "amount")
    assert len(ref.rows) == 3


def test_cross_user_reference_is_denied():
    asset_service, references = build_stack()
    asset = make_table(asset_service)
    with pytest.raises(PermissionError, match="E_ASSET_FORBIDDEN"):
        references.resolve(asset.id, AccessContext(tenant_id=TENANT, user_id=OTHER_USER))


def test_cross_tenant_reference_is_denied_even_for_admin():
    asset_service, references = build_stack()
    asset = make_table(asset_service)
    outsider = AccessContext(tenant_id="tenant-b", user_id=OWNER, roles=("admin",))
    with pytest.raises(PermissionError):
        references.resolve(asset.id, outsider)


def test_expired_asset_cannot_be_referenced():
    asset_service, references = build_stack()
    asset = make_table(
        asset_service, expires_at=FIXED_NOW - timedelta(seconds=1)
    )
    with pytest.raises(PermissionError, match="E_ASSET_EXPIRED"):
        references.resolve(asset.id, owner_context())


def test_column_subset_is_projected():
    asset_service, references = build_stack()
    asset = make_table(asset_service)
    ref = references.resolve(asset.id, owner_context(), columns=["amount"])
    assert ref.columns == ("amount",)
    assert all(len(row) == 1 for row in ref.rows)


def test_unknown_column_is_rejected_not_silently_dropped():
    asset_service, references = build_stack()
    asset = make_table(asset_service)
    with pytest.raises(ValueError, match="E_REFERENCE_COLUMN"):
        references.resolve(asset.id, owner_context(), columns=["region", "secret"])


def test_row_budget_is_enforced():
    asset_service, references = build_stack()
    # Keep the source under the 100-row preview ceiling so this exercises the
    # reference budget rather than the preview limit.
    rows = [[f"r{index}", str(index)] for index in range(30)]
    asset = make_table(asset_service, rows=rows)
    ref = references.resolve(asset.id, owner_context(), max_rows=10)
    assert len(ref.rows) == 10
    assert ref.truncated is True
    assert ref.total_rows == 30


def test_preview_ceiling_is_not_confused_with_original_size():
    """An asset only stores a bounded preview; total_rows reflects that."""
    asset_service, references = build_stack()
    rows = [[f"r{index}", str(index)] for index in range(500)]
    asset = make_table(asset_service, rows=rows)
    ref = references.resolve(asset.id, owner_context())
    # 500 source rows collapse to a 100-row preview, then the reference budget
    # trims again to the configured ceiling.
    assert ref.total_rows == 100
    assert len(ref.rows) == references.max_rows
    assert ref.truncated is True


def test_reference_cap_cannot_be_exceeded_by_caller():
    asset_service, references = build_stack()
    rows = [[f"r{index}", str(index)] for index in range(500)]
    asset = make_table(asset_service, rows=rows)
    ref = references.resolve(asset.id, owner_context(), max_rows=100000)
    assert len(ref.rows) <= references.max_rows


def test_provenance_travels_with_reference():
    asset_service, references = build_stack()
    asset = make_table(asset_service)
    ref = references.resolve(asset.id, owner_context())
    assert ref.semantic_version == "sem-1"
    assert ref.data_version == "sales@2026-09-12"
    assert ref.referenced_at


def test_sql_asset_reference_returns_no_rows():
    asset_service, references = build_stack()
    asset = asset_service.create_sql(
        task_id="t1", context=owner_context(), sql="SELECT 1"
    )
    ref = references.resolve(asset.id, owner_context())
    assert ref.kind == "sql"
    assert ref.rows == ()


def test_unsupported_kind_is_rejected():
    asset_service, references = build_stack()
    asset = asset_service.create_report(
        task_id="t1", context=owner_context(), markdown="# 报告"
    )
    with pytest.raises(ValueError, match="E_REFERENCE_KIND"):
        references.resolve(asset.id, owner_context())


def test_model_context_is_bounded_and_uses_only_authorized_values():
    asset_service, references = build_stack()
    rows = [["华东", "100"], ["华南", "200"]]
    asset = make_table(asset_service, rows=rows)
    ref = references.resolve(asset.id, owner_context())
    text = references.to_model_context(ref)
    assert "华东" in text
    assert "100" in text
    assert len(text.encode("utf-8")) <= references.max_context_bytes
