import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import mysql_migrate


def test_legacy_demo_utf8_repair_is_scoped_and_idempotent():
    migration = Path(__file__).parents[1] / "migrations" / "mysql" / "V017__repair_legacy_demo_utf8.sql"
    statements = mysql_migrate._statements(migration)
    mojibake_east = bytes.fromhex("C3A5C28DC5BDC3A4C2B8C593").decode("utf-8")
    mojibake_south = bytes.fromhex("C3A5C28DC5BDC3A5C28DE28094").decode("utf-8")
    mojibake_name = bytes.fromhex(
        "C3A6CB9CC5B8C3A6C2B2C2B3C3A7C2A7E28098C3A6C5A0E282AC"
    ).decode("utf-8")

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            "CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT);"
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, region TEXT);"
        )
        conn.executemany(
            "INSERT INTO sales(id, region) VALUES(?, ?)",
            [(1, mojibake_east), (2, mojibake_south), (3, "华东"), (99, mojibake_east)],
        )
        conn.executemany(
            "INSERT INTO customers(id, name, region) VALUES(?, ?, ?)",
            [(1, mojibake_name, mojibake_east), (2, "Café", "华南")],
        )

        for _ in range(2):
            for statement in statements:
                conn.execute(statement)

        assert conn.execute("SELECT region FROM sales WHERE id=1").fetchone()[0] == "华东"
        assert conn.execute("SELECT region FROM sales WHERE id=2").fetchone()[0] == "华南"
        assert conn.execute("SELECT region FROM sales WHERE id=3").fetchone()[0] == "华东"
        assert conn.execute("SELECT region FROM sales WHERE id=99").fetchone()[0] == mojibake_east
        assert conn.execute("SELECT name, region FROM customers WHERE id=1").fetchone() == ("星河科技", "华东")
        assert conn.execute("SELECT name, region FROM customers WHERE id=2").fetchone() == ("Café", "华南")


def test_legacy_result_asset_repair_requires_exact_demo_shape_and_bytes():
    migration = Path(__file__).parents[1] / "migrations" / "mysql" / "V018__repair_legacy_demo_result_assets.sql"
    statements = mysql_migrate._statements(migration)

    assert len(statements) == 1
    statement = statements[0]
    assert "WHERE kind = 'table'" in statement
    assert "'$.columns[0]'" in statement and "= 'region'" in statement
    assert "'$.columns[1]'" in statement and "= 'total_amount'" in statement
    assert "JSON_LENGTH(JSON_EXTRACT(metadata_json, '$.rows')) = 2" in statement
    assert "C3A5C28DC5BDC3A4C2B8C593" in statement
    assert "C3A5C28DC5BDC3A5C28DE28094" in statement


def test_migration_plan_sorts_and_skips_dba_reserved_version(tmp_path):
    migrations = tmp_path / "mysql"
    migrations.mkdir()
    (migrations / "V003__later.sql").write_text("CREATE TABLE later (id INT);", encoding="utf-8")
    (migrations / "V002__dba.sql").write_text("GRANT SELECT ON *.* TO app;", encoding="utf-8")
    (migrations / "V001__first.sql").write_text("CREATE TABLE first (id INT);", encoding="utf-8")

    plan = mysql_migrate.migration_plan(migrations)

    assert [item.version for item in plan] == ["V001", "V003"]
    assert [len(item.statements) for item in plan] == [1, 1]


def test_migration_plan_filters_applied_versions(tmp_path):
    migrations = tmp_path / "mysql"
    migrations.mkdir()
    (migrations / "V001__first.sql").write_text("CREATE TABLE first (id INT);", encoding="utf-8")
    (migrations / "V004__second.sql").write_text("CREATE TABLE second (id INT);", encoding="utf-8")

    plan = mysql_migrate.migration_plan(migrations, {"V001"})

    assert [item.version for item in plan] == ["V004"]


def test_branch_analysis_migration_adds_indexed_task_lineage():
    migration = Path(__file__).parents[1] / "migrations" / "mysql" / "V026__add_task_analysis_branches.sql"
    statements = mysql_migrate._statements(migration)
    joined = "\n".join(statements)

    assert "parent_task_id" in joined
    assert "root_task_id" in joined
    assert "branch_source_trace_id" in joined
    assert "branch_depth" in joined
    assert "idx_data_tasks_branch_parent" in joined
    assert "idx_data_tasks_branch_root" in joined


def test_proactive_analysis_migration_adds_plans_runs_and_due_indexes():
    migration = Path(__file__).parents[1] / "migrations" / "mysql" / "V027__create_proactive_analysis.sql"
    statements = mysql_migrate._statements(migration)
    joined = "\n".join(statements)

    assert "proactive_analysis_plans" in joined
    assert "proactive_analysis_runs" in joined
    assert "idx_proactive_plans_due" in joined
    assert "idx_proactive_runs_plan" in joined
    assert "lease_token" in joined and "lease_expires_at" in joined


def test_dry_run_does_not_connect_to_mysql(tmp_path, monkeypatch, capsys):
    migrations = tmp_path / "mysql"
    migrations.mkdir()
    (migrations / "V001__first.sql").write_text("CREATE TABLE first (id INT);", encoding="utf-8")

    def fail_connect(*args, **kwargs):
        raise AssertionError("dry-run must not connect")

    monkeypatch.setattr(mysql_migrate.pymysql, "connect", fail_connect)
    mysql_migrate.main(["--dry-run", "--migrations-dir", str(migrations)])

    output = capsys.readouterr().out
    assert "no database connection" in output
    assert "V001: V001__first.sql (1 statements)" in output


def test_dry_run_writes_machine_readable_report(tmp_path, monkeypatch):
    migrations = tmp_path / "mysql"
    migrations.mkdir()
    migration = migrations / "V001__first.sql"
    migration.write_text("CREATE TABLE first (id INT);", encoding="utf-8")
    report = tmp_path / "migration-plan.json"
    def fail_connect(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(mysql_migrate.pymysql, "connect", fail_connect)

    code = mysql_migrate.main(
        ["--dry-run", "--migrations-dir", str(migrations), "--report", str(report)]
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["format"] == "query-pilot-mysql-migration-plan"
    assert payload["database_connected"] is False
    assert payload["database_state_checked"] is False
    assert payload["planned_count"] == 1
    assert payload["migrations"][0]["file"] == migration.name
    assert len(payload["migrations"][0]["sha256"]) == 64


def test_direct_script_execution_resolves_project_package_without_pythonpath(tmp_path):
    migrations = tmp_path / "mysql"
    migrations.mkdir()
    (migrations / "V001__first.sql").write_text(
        "CREATE TABLE first (id INT);", encoding="utf-8"
    )
    report = tmp_path / "migration-plan.json"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "mysql_migrate.py"),
            "--dry-run",
            "--migrations-dir",
            str(migrations),
            "--report",
            str(report),
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert report.exists()
    assert json.loads(report.read_text(encoding="utf-8"))["planned_count"] == 1


def test_dry_run_report_refuses_to_overwrite_migration(tmp_path):
    migrations = tmp_path / "mysql"
    migrations.mkdir()
    migration = migrations / "V001__first.sql"
    original = "CREATE TABLE first (id INT);"
    migration.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        mysql_migrate.main(
            ["--dry-run", "--migrations-dir", str(migrations), "--report", str(migration)]
        )

    assert migration.read_text(encoding="utf-8") == original


def test_migration_connection_uses_application_env_file_fallback(monkeypatch):
    for name in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(mysql_migrate.settings, "mysql_host", "127.0.0.9")
    monkeypatch.setattr(mysql_migrate.settings, "mysql_port", 3307)
    monkeypatch.setattr(mysql_migrate.settings, "mysql_user", "local_user")
    monkeypatch.setattr(mysql_migrate.settings, "mysql_password", "local_password")
    monkeypatch.setattr(mysql_migrate.settings, "mysql_database", "local_database")

    options = mysql_migrate.mysql_connection_options()

    assert options["host"] == "127.0.0.9"
    assert options["port"] == 3307
    assert options["user"] == "local_user"
    assert options["password"] == "local_password"
    assert options["database"] == "local_database"
