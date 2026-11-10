from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from scripts.seed_multimonth_demo_data import (
    _resolve_oracle_home,
    _wait_for_postgres,
    build_demo_rows,
    build_oracle_merge,
    build_postgres_upsert,
)


def test_demo_rows_cover_twelve_months_and_four_regions_deterministically():
    rows = build_demo_rows(end_month=date(2026, 11, 1), months=12)

    assert len(rows) == 48
    assert len({row.sold_at.strftime("%Y-%m") for row in rows}) == 12
    assert {row.region for row in rows} == {"华东", "华南", "华北", "西南"}
    assert min(row.id for row in rows) >= 910000
    assert rows == build_demo_rows(end_month=date(2026, 11, 1), months=12)


def test_demo_rows_include_a_visible_but_bounded_monthly_spike():
    rows = build_demo_rows(end_month=date(2026, 11, 1), months=12)
    totals = {}
    for row in rows:
        month = row.sold_at.strftime("%Y-%m")
        totals[month] = totals.get(month, 0) + row.amount

    ordered = sorted(totals.values(), reverse=True)
    assert ordered[0] > ordered[1] * Decimal("1.4")
    assert all(total > 0 for total in totals.values())


def test_postgres_builder_is_idempotent_and_uses_typed_dates():
    sql = build_postgres_upsert(build_demo_rows(end_month=date(2026, 11, 1), months=12))

    assert "INSERT INTO sales" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    assert "DATE '2025-12-" in sql
    assert "DATE '2026-11-" in sql


def test_postgres_wait_retries_until_container_is_ready(monkeypatch):
    return_codes = iter((1, 1, 0))
    calls = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result(next(return_codes))

    ticks = iter((0.0, 0.1, 0.2, 0.3, 0.4))
    monkeypatch.setattr("scripts.seed_multimonth_demo_data.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.seed_multimonth_demo_data.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("scripts.seed_multimonth_demo_data.time.sleep", lambda _seconds: None)

    _wait_for_postgres("query-pilot-postgres-postgres-1", wait_seconds=5)

    assert len(calls) == 3
    assert calls[0][-5:] == ["pg_isready", "-U", "query_app", "-d", "query_agent"]


def test_oracle_builder_is_idempotent_and_scoped_to_validated_owner():
    sql = build_oracle_merge(
        build_demo_rows(end_month=date(2026, 11, 1), months=12),
        schema_owner="QUERYPILOT_OWNER",
    )

    assert "MERGE INTO QUERYPILOT_OWNER.sales" in sql
    assert "TIMESTAMP '2025-12-" in sql
    assert "TIMESTAMP '2026-11-" in sql
    assert "WHEN MATCHED THEN UPDATE" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql

    with pytest.raises(ValueError, match="schema owner"):
        build_oracle_merge([], schema_owner="OWNER; DROP TABLE SALES")


def test_oracle_home_requires_explicit_portable_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("ORACLE_HOME", raising=False)
    with pytest.raises(ValueError, match="ORACLE_HOME"):
        _resolve_oracle_home(None)

    configured = tmp_path / "oracle-home"
    monkeypatch.setenv("ORACLE_HOME", str(configured))
    assert _resolve_oracle_home(None) == configured

    explicit = tmp_path / "explicit-home"
    assert _resolve_oracle_home(explicit) == explicit
