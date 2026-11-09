"""Pure unit tests for result preview normalization. No database required."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.services.result_preview_service import (
    NOTE_BINARY,
    NOTE_BINARY_BASE64,
    NOTE_COERCED,
    NOTE_DECIMAL,
    NOTE_NON_FINITE,
    NOTE_TEMPORAL,
    NOTE_TRUNCATED,
    REASON_CELL_BUDGET,
    REASON_ROW_BUDGET,
    REASON_ROW_LIMIT,
    REASON_SOURCE_TRUNCATED,
    REASON_TOTAL_BUDGET,
    PreviewBudget,
    build_result_preview,
    normalize_value,
    strict_json_safe,
)

SAMPLE_UUID = UUID("12345678-1234-5678-1234-567812345678")


# --- numeric precision ---


def test_decimal_keeps_precision_as_string():
    value, note = normalize_value(Decimal("1.10"))
    assert value == "1.10"
    assert note == NOTE_DECIMAL


def test_decimal_does_not_degrade_to_float():
    # A float round trip would collapse these two distinct values.
    assert normalize_value(Decimal("0.1"))[0] == "0.1"
    assert normalize_value(Decimal("0.100"))[0] == "0.100"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_float_becomes_null(value):
    result, note = normalize_value(value)
    assert result is None
    assert note == NOTE_NON_FINITE


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimal_becomes_null(value):
    result, note = normalize_value(value)
    assert result is None
    assert note == NOTE_NON_FINITE


def test_non_finite_value_does_not_break_strict_json():
    payload = build_result_preview(["amount"], [[float("nan")], [float("inf")]])
    assert payload["rows"] == [[None], [None]]
    strict_json_safe(payload)


# --- temporal and identifier types ---


def test_datetime_becomes_iso_string():
    value, note = normalize_value(datetime(2026, 9, 12, 10, 30))
    assert value == "2026-09-12T10:30:00"
    assert note == NOTE_TEMPORAL


def test_date_and_time_become_iso_strings():
    assert normalize_value(date(2026, 9, 12))[0] == "2026-09-12"
    assert normalize_value(time(10, 30))[0] == "10:30:00"


def test_timedelta_becomes_seconds():
    assert normalize_value(timedelta(hours=2))[0] == 7200.0


def test_uuid_becomes_string():
    assert normalize_value(SAMPLE_UUID)[0] == "12345678-1234-5678-1234-567812345678"


# --- binary handling ---


def test_binary_is_omitted_by_default():
    value, note = normalize_value(b"\x89PNG-secret")
    assert value == "[binary omitted:11 bytes]"
    assert note == NOTE_BINARY
    assert "PNG" not in value.replace("[binary omitted:", "")


def test_binary_base64_only_when_policy_enabled_and_small():
    budget = PreviewBudget(bytes_policy="base64", base64_limit_bytes=256)
    value, note = normalize_value(b"tiny", budget)
    assert note == NOTE_BINARY_BASE64
    assert value == "dGlueQ=="


def test_oversized_binary_is_omitted_even_under_base64_policy():
    budget = PreviewBudget(bytes_policy="base64", base64_limit_bytes=4)
    value, note = normalize_value(b"much-too-large", budget)
    assert value == "[binary omitted:14 bytes]"
    assert note == NOTE_BINARY


def test_bytes_policy_is_validated():
    with pytest.raises(ValueError):
        PreviewBudget(bytes_policy="leak")


# --- collections and unknown objects ---


def test_set_becomes_stable_sorted_list():
    value, _ = normalize_value({"b", "a"})
    assert value == ["a", "b"]


def test_unknown_object_is_coerced_to_string():
    class Custom:
        def __str__(self):
            return "custom-value"

    value, note = normalize_value(Custom())
    assert value == "custom-value"
    assert note == NOTE_COERCED


def test_none_and_bool_pass_through():
    assert normalize_value(None) == (None, "")
    assert normalize_value(True) == (True, "")


# --- capacity budgets ---


def test_cell_budget_truncates_long_text():
    budget = PreviewBudget(max_cell_bytes=32)
    value, note = normalize_value("x" * 500, budget)
    assert note == NOTE_TRUNCATED
    assert value.endswith("…[truncated]")
    assert len(value.encode("utf-8")) <= 32


def test_cell_truncation_keeps_multibyte_characters_intact():
    budget = PreviewBudget(max_cell_bytes=40)
    value, _ = normalize_value("中" * 200, budget)
    assert "�" not in value


def test_row_budget_stops_adding_cells():
    budget = PreviewBudget(max_row_bytes=20)
    payload = build_result_preview(["a", "b"], [["x" * 15, "y" * 15]], budget=budget)
    assert REASON_ROW_BUDGET in payload["truncation_reasons"]


def test_oversized_rows_do_not_loop_forever():
    budget = PreviewBudget(max_rows=100, max_row_bytes=1)
    payload = build_result_preview(["a"], [["x" * 500] for _ in range(5000)], budget=budget)
    assert payload["truncated"] is True
    assert REASON_ROW_BUDGET in payload["truncation_reasons"]


def test_total_budget_stops_processing_rows():
    budget = PreviewBudget(max_rows=10, max_total_bytes=30)
    payload = build_result_preview(["a"], [["x" * 10] for _ in range(5)], budget=budget)
    assert REASON_TOTAL_BUDGET in payload["truncation_reasons"]
    assert payload["preview_count"] < 5
    assert payload["byte_size"] <= budget.max_total_bytes


def test_row_limit_is_recorded():
    budget = PreviewBudget(max_rows=2)
    payload = build_result_preview(["a"], [[1], [2], [3], [4]], budget=budget)
    assert payload["preview_count"] == 2
    assert payload["row_count"] == 4
    assert REASON_ROW_LIMIT in payload["truncation_reasons"]


def test_source_truncation_is_preserved():
    payload = build_result_preview(["a"], [[1]], source_truncated=True)
    assert REASON_SOURCE_TRUNCATED in payload["truncation_reasons"]
    assert payload["truncated"] is True


def test_untruncated_result_has_no_reasons():
    payload = build_result_preview(["a"], [[1], [2]])
    assert payload["truncation_reasons"] == []
    assert payload["truncated"] is False


# --- payload contract ---


def test_payload_keeps_existing_artifact_field_names():
    payload = build_result_preview(["region"], [["华东"], ["华南"]])
    for key in ("columns", "rows", "row_count", "preview_count", "preview_limit", "truncated"):
        assert key in payload


def test_default_preview_limit_matches_existing_behavior():
    payload = build_result_preview(["a"], [[index] for index in range(250)])
    assert payload["preview_limit"] == 100
    assert payload["preview_count"] == 100


def test_extra_metadata_is_merged():
    payload = build_result_preview(["a"], [[1]], extra_metadata={"data_scope": "redacted"})
    assert payload["data_scope"] == "redacted"


def test_normalized_notes_group_by_column():
    payload = build_result_preview(["amount"], [[Decimal("1.5")], [float("nan")]])
    assert payload["normalized_notes"]["amount"] == [NOTE_DECIMAL, NOTE_NON_FINITE]


def test_empty_result_is_safe():
    payload = build_result_preview([], [])
    assert payload["rows"] == []
    assert payload["row_count"] == 0
    assert payload["truncated"] is False


def test_mixed_types_in_one_row():
    row = [Decimal("9.99"), datetime(2026, 9, 12), SAMPLE_UUID, None, float("inf")]
    payload = build_result_preview(["price", "created", "id", "note", "ratio"], [row])
    assert payload["rows"] == [["9.99", "2026-09-12T00:00:00", str(SAMPLE_UUID), None, None]]


def test_strict_json_safe_rejects_raw_non_finite():
    with pytest.raises(ValueError):
        strict_json_safe({"bad": float("nan")})
