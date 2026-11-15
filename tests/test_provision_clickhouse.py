from __future__ import annotations

from scripts.provision_clickhouse import build_statements


def test_build_statements_adds_monthly_demo_contract_idempotently():
    statements = build_statements(database="acceptance")
    joined = "\n".join(statements)

    assert "CREATE DATABASE IF NOT EXISTS `acceptance`" in joined
    assert "CREATE TABLE IF NOT EXISTS `acceptance`.sales" in joined
    assert "sold_at Date" in joined
    assert "ADD COLUMN IF NOT EXISTS sold_at Date" in joined
    assert "addMonths" in joined and "mutations_sync = 2" in joined
    assert "2026-06-15" in joined and "2026-08-15" in joined
    assert "WHERE NOT EXISTS" in joined


def test_build_statements_rejects_unsafe_database_identifier():
    try:
        build_statements(database="acceptance; DROP DATABASE default")
    except ValueError as exc:
        assert "database" in str(exc)
    else:
        raise AssertionError("unsafe database identifier was accepted")
