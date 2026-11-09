from __future__ import annotations

from pathlib import Path

import pytest

from scripts.provision_sqlserver import build_statements, main

BASE = {
    "SQLSERVER_HOST": "127.0.0.1",
    "SQLSERVER_DATABASE": "acceptance",
    "SQLSERVER_USER": "qpro_ro",
    "SQLSERVER_PASSWORD": "ReadOnly-Pass-123",
    "SQLSERVER_ADMIN_PASSWORD": "Admin-Pass-123",
}


def _env_file(tmp_path: Path, **overrides) -> Path:
    values = {**BASE, **overrides}
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    return path


def test_build_statements_creates_database_tables_foreign_key_and_select_grant():
    statements = build_statements(
        database="acceptance",
        readonly_login="qpro_ro",
        readonly_password="ReadOnly-Pass-123",
    )
    joined = "\n".join(statements)

    assert "CREATE DATABASE [acceptance]" in joined
    assert "CREATE TABLE [acceptance].dbo.customers" in joined
    assert "CREATE TABLE [acceptance].dbo.sales" in joined
    assert "CONSTRAINT fk_sales_customer FOREIGN KEY" in joined
    assert "CREATE LOGIN [qpro_ro]" in joined
    assert "GRANT SELECT ON SCHEMA::dbo TO [qpro_ro]" in joined
    # At least two sales rows are required by the acceptance probes.
    assert joined.count("1234.56") == 1 and "987.65" in joined


def test_provisioning_refuses_identifiers_that_could_inject_sql():
    with pytest.raises(ValueError, match="database"):
        build_statements(database="acceptance; DROP DATABASE master", readonly_login="qpro", readonly_password="12345678")
    with pytest.raises(ValueError, match="readonly login"):
        build_statements(database="acceptance", readonly_login="qpro'--", readonly_password="12345678")
    with pytest.raises(ValueError, match="password"):
        build_statements(database="acceptance", readonly_login="qpro", readonly_password="short")


def test_password_quotes_are_escaped():
    statements = build_statements(
        database="acceptance",
        readonly_login="qpro",
        readonly_password="penny'worth-123",
    )
    joined = "\n".join(statements)
    assert "penny''worth-123" in joined


def test_dry_run_masks_the_password_and_never_connects(tmp_path, capsys):
    env_file = _env_file(tmp_path)

    assert main(["--dry-run", "--env-file", str(env_file)]) == 0
    output = capsys.readouterr().out

    assert "CREATE DATABASE [acceptance]" in output
    assert "ReadOnly-Pass-123" not in output
    assert "***" in output


def test_missing_configuration_is_reported_without_connecting(tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("SQLSERVER_HOST=\n", encoding="utf-8")

    assert main(["--dry-run", "--env-file", str(env_file)]) == 2
    assert "Missing configuration" in capsys.readouterr().out
