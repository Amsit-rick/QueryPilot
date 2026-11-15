r"""Seed deterministic multi-month analytics data into local demo databases.

This script is intentionally limited to reserved IDs starting at 910000. It
does not delete or rewrite the original demo rows and is safe to run repeatedly.

Windows CMD examples from the project root:

    .venv\Scripts\python.exe scripts\seed_multimonth_demo_data.py ^
      --postgres-container query-pilot-postgres-postgres-1

    .venv\Scripts\python.exe scripts\seed_multimonth_demo_data.py ^
      --oracle-sysdba --oracle-pdb ORCLPDB1
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_REGIONS = ("华东", "华南", "华北", "西南")
_WEIGHTS = (Decimal("0.34"), Decimal("0.26"), Decimal("0.22"), Decimal("0.18"))


@dataclass(frozen=True)
class DemoSale:
    id: int
    customer_id: int
    region: str
    amount: Decimal
    sold_at: date


def _month_shift(value: date, offset: int) -> date:
    absolute = value.year * 12 + value.month - 1 + offset
    return date(absolute // 12, absolute % 12 + 1, 1)


def build_demo_rows(*, end_month: date, months: int = 12) -> list[DemoSale]:
    if end_month.day != 1:
        raise ValueError("end month must be the first day of a month")
    if not 4 <= months <= 36:
        raise ValueError("months must be between 4 and 36")
    start = _month_shift(end_month, -(months - 1))
    monthly_totals = [Decimal(7200 + index * 550) for index in range(months)]
    monthly_totals[max(0, months - 2)] *= Decimal("1.85")
    rows: list[DemoSale] = []
    for month_index, total in enumerate(monthly_totals):
        month = _month_shift(start, month_index)
        allocated = Decimal("0")
        for region_index, (region, weight) in enumerate(zip(_REGIONS, _WEIGHTS)):
            amount = (total - allocated) if region_index == len(_REGIONS) - 1 else (total * weight).quantize(Decimal("0.01"))
            allocated += amount
            rows.append(
                DemoSale(
                    id=910000 + month_index * 10 + region_index + 1,
                    customer_id=1 if region_index % 2 == 0 else 2,
                    region=region,
                    amount=amount,
                    sold_at=date(month.year, month.month, 5 + region_index * 5),
                )
            )
    return rows


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sales_values(rows: list[DemoSale], *, typed_date: bool = False) -> str:
    date_prefix = "DATE " if typed_date else ""
    return ",\n    ".join(
        f"({row.id}, {_literal(row.region)}, {row.amount}, {date_prefix}{_literal(row.sold_at.isoformat())})"
        for row in rows
    )


def build_mysql_upsert(rows: list[DemoSale], *, replace_sales: bool = False) -> str:
    if not rows:
        raise ValueError("demo rows cannot be empty")
    prefix = "DELETE FROM sales;\n" if replace_sales else ""
    return (
        prefix
        + "INSERT INTO sales (id, region, amount, sold_at) VALUES\n    "
        + _sales_values(rows)
        + "\nON DUPLICATE KEY UPDATE "
        "region=VALUES(region), amount=VALUES(amount), sold_at=VALUES(sold_at);"
    )


def build_postgres_upsert(rows: list[DemoSale], *, replace_sales: bool = False) -> str:
    if not rows:
        raise ValueError("demo rows cannot be empty")
    prefix = "DELETE FROM sales;\n" if replace_sales else ""
    return (
        prefix
        + "INSERT INTO sales (id, region, amount, sold_at) VALUES\n    "
        + _sales_values(rows, typed_date=True)
        + "\nON CONFLICT (id) DO UPDATE SET "
        "region=EXCLUDED.region, amount=EXCLUDED.amount, sold_at=EXCLUDED.sold_at;"
    )


def _validated_identifier(value: str, label: str) -> str:
    normalized = str(value or "").strip().upper()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{label} must be a simple SQL identifier")
    return normalized


def build_oracle_merge(
    rows: list[DemoSale],
    *,
    schema_owner: str,
    replace_sales: bool = False,
) -> str:
    owner = _validated_identifier(schema_owner, "schema owner")
    if not rows:
        raise ValueError("demo rows cannot be empty")
    source = "\nUNION ALL\n".join(
        "SELECT "
        f"{row.id} AS id, {row.customer_id} AS customer_id, {_literal(row.region)} AS region, "
        f"{row.amount} AS amount, TIMESTAMP '{row.sold_at.isoformat()} 10:00:00' AS created_at FROM dual"
        for row in rows
    )
    prefix = f"DELETE FROM {owner}.sales;\n" if replace_sales else ""
    return prefix + f"""MERGE INTO {owner}.sales target
USING (
{source}
) source
ON (target.id = source.id)
WHEN MATCHED THEN UPDATE SET
    target.customer_id = source.customer_id,
    target.region = source.region,
    target.amount = source.amount,
    target.created_at = source.created_at
WHEN NOT MATCHED THEN INSERT (id, customer_id, region, amount, created_at)
VALUES (source.id, source.customer_id, source.region, source.amount, source.created_at)"""


def build_sqlserver_merge(
    rows: list[DemoSale],
    *,
    database: str,
    replace_sales: bool = False,
) -> str:
    database_name = str(database or "").strip()
    if not _IDENTIFIER.fullmatch(database_name):
        raise ValueError("database must be a simple SQL identifier")
    if not rows:
        raise ValueError("demo rows cannot be empty")
    table = f"[{database_name}].dbo.sales"
    source = ",\n    ".join(
        f"({row.id}, {row.customer_id}, N{_literal(row.region)}, {row.amount}, CAST({_literal(row.sold_at.isoformat())} AS date))"
        for row in rows
    )
    prefix = f"DELETE FROM {table};\n" if replace_sales else ""
    return prefix + f"""MERGE INTO {table} AS target
USING (VALUES
    {source}
) AS source (id, customer_id, region, amount, sold_at)
ON target.id = source.id
WHEN MATCHED THEN UPDATE SET
    customer_id=source.customer_id,
    region=source.region,
    amount=source.amount,
    sold_at=source.sold_at
WHEN NOT MATCHED THEN INSERT (id, customer_id, region, amount, sold_at)
VALUES (source.id, source.customer_id, source.region, source.amount, source.sold_at);"""


def build_clickhouse_statements(
    rows: list[DemoSale],
    *,
    database: str,
    replace_sales: bool = False,
) -> list[str]:
    database_name = str(database or "").strip()
    if not _IDENTIFIER.fullmatch(database_name):
        raise ValueError("database must be a simple SQL identifier")
    if not rows:
        raise ValueError("demo rows cannot be empty")
    table = f"`{database_name}`.sales"
    cleanup = (
        f"TRUNCATE TABLE {table}"
        if replace_sales
        else f"ALTER TABLE {table} DELETE WHERE id >= 910000 AND id < 920000 SETTINGS mutations_sync = 2"
    )
    values = ",\n    ".join(
        f"({row.id}, {row.customer_id}, {_literal(row.region)}, "
        f"toDecimal64({_literal(str(row.amount))}, 2), toDate({_literal(row.sold_at.isoformat())}))"
        for row in rows
    )
    insert = (
        f"INSERT INTO {table} (id, customer_id, region, amount, sold_at) VALUES\n    "
        + values
    )
    return [cleanup, insert]


def _parse_month(value: str) -> date:
    try:
        year, month = (int(part) for part in value.split("-", 1))
        return date(year, month, 1)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from exc


def _run_postgres(container: str, sql: str) -> None:
    if not _CONTAINER.fullmatch(container):
        raise ValueError("PostgreSQL container must be a simple Docker container name")
    subprocess.run(
        ["docker", "exec", container, "psql", "-U", "query_app", "-d", "query_agent", "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
    )


def _run_sqlite(path: Path, rows: list[DemoSale], *, replace_sales: bool) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    with sqlite3.connect(path) as connection:
        if replace_sales:
            connection.execute("DELETE FROM sales")
        connection.executemany(
            """INSERT INTO sales (id, region, amount, sold_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                region=excluded.region, amount=excluded.amount, sold_at=excluded.sold_at""",
            [(row.id, row.region, str(row.amount), row.sold_at.isoformat()) for row in rows],
        )


def _run_mysql(rows: list[DemoSale], *, replace_sales: bool) -> None:
    from app.mysql_db import connect

    with connect() as connection:
        with connection.cursor() as cursor:
            if replace_sales:
                cursor.execute("DELETE FROM sales")
            cursor.execute(build_mysql_upsert(rows))
        connection.commit()


def _run_sqlserver(rows: list[DemoSale], *, env_file: Path, replace_sales: bool) -> None:
    from scripts.provision_sqlserver import _connect, _read_env, _setting

    values = _read_env(env_file)
    host = _setting("SQLSERVER_HOST", values)
    port = int(_setting("SQLSERVER_PORT", values, "1433"))
    database = _setting("SQLSERVER_DATABASE", values)
    user = _setting("SQLSERVER_ADMIN_USER", values, "sa")
    password = _setting("SQLSERVER_ADMIN_PASSWORD", values)
    if not all((host, database, user, password)):
        raise ValueError("SQL Server administrative configuration is incomplete")
    connection = _connect(
        server=host,
        port=str(port),
        user=user,
        password=password,
        database="master",
        login_timeout=10,
    )
    try:
        connection.autocommit(True)
        connection.cursor().execute(
            build_sqlserver_merge(rows, database=database, replace_sales=replace_sales)
        )
    finally:
        connection.close()


def _run_clickhouse(container: str, statements: list[str]) -> None:
    if not _CONTAINER.fullmatch(container):
        raise ValueError("ClickHouse container must be a simple Docker container name")
    for statement in statements:
        subprocess.run(
            ["docker", "exec", container, "clickhouse-client", "--query", statement],
            check=True,
            capture_output=True,
            text=True,
        )


def _wait_for_postgres(container: str, *, wait_seconds: int) -> None:
    if not _CONTAINER.fullmatch(container):
        raise ValueError("PostgreSQL container must be a simple Docker container name")
    if not 1 <= wait_seconds <= 300:
        raise ValueError("PostgreSQL wait seconds must be between 1 and 300")
    deadline = time.monotonic() + wait_seconds
    command = ["docker", "exec", container, "pg_isready", "-U", "query_app", "-d", "query_agent"]
    while True:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"PostgreSQL did not become ready within {wait_seconds} seconds")
        time.sleep(1)


def _run_oracle_sysdba(*, sqlplus: Path, pdb: str, merge_sql: str) -> None:
    pdb_name = _validated_identifier(pdb, "Oracle PDB")
    if not sqlplus.is_file():
        raise FileNotFoundError(f"sqlplus not found: {sqlplus}")
    script = f"""WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF HEADING OFF FEEDBACK OFF PAGESIZE 0
ALTER SESSION SET CONTAINER = {pdb_name};
{merge_sql};
COMMIT;
EXIT SUCCESS
"""
    subprocess.run(
        [str(sqlplus), "-S", "/ as sysdba"],
        input=script,
        check=True,
        capture_output=True,
        text=True,
    )


def _resolve_oracle_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    configured = os.getenv("ORACLE_HOME", "").strip()
    if not configured:
        raise ValueError("Oracle seeding requires --oracle-home or ORACLE_HOME")
    return Path(configured)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-month", type=_parse_month, default=date.today().replace(day=1))
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--sqlite-path", type=Path)
    parser.add_argument("--mysql", action="store_true")
    parser.add_argument("--postgres-container")
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--sqlserver", action="store_true")
    parser.add_argument("--clickhouse-container")
    parser.add_argument("--oracle-sysdba", action="store_true")
    parser.add_argument("--oracle-pdb", default="ORCLPDB1")
    parser.add_argument("--oracle-schema-owner", default="QUERYPILOT_OWNER")
    parser.add_argument(
        "--oracle-home",
        type=Path,
        default=None,
    )
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--replace-sales",
        action="store_true",
        help="replace the local demo sales table so every datasource has the same rows",
    )
    args = parser.parse_args(argv)
    if not any(
        (
            args.sqlite_path,
            args.mysql,
            args.postgres_container,
            args.sqlserver,
            args.clickhouse_container,
            args.oracle_sysdba,
        )
    ):
        parser.error("select at least one target")
    try:
        rows = build_demo_rows(end_month=args.end_month, months=args.months)
        if args.sqlite_path:
            _run_sqlite(args.sqlite_path, rows, replace_sales=args.replace_sales)
            print(f"SQLite demo data seeded: rows={len(rows)} months={args.months}")
        if args.mysql:
            _run_mysql(rows, replace_sales=args.replace_sales)
            print(f"MySQL demo data seeded: rows={len(rows)} months={args.months}")
        if args.postgres_container:
            if args.wait_seconds:
                _wait_for_postgres(args.postgres_container, wait_seconds=args.wait_seconds)
            _run_postgres(
                args.postgres_container,
                build_postgres_upsert(rows, replace_sales=args.replace_sales),
            )
            print(f"PostgreSQL demo data seeded: rows={len(rows)} months={args.months}")
        if args.sqlserver:
            _run_sqlserver(rows, env_file=args.env_file, replace_sales=args.replace_sales)
            print(f"SQL Server demo data seeded: rows={len(rows)} months={args.months}")
        if args.clickhouse_container:
            database = ""
            if args.env_file.is_file():
                for line in args.env_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("CLICKHOUSE_DATABASE="):
                        database = line.split("=", 1)[1].strip().strip("'\"")
                        break
            _run_clickhouse(
                args.clickhouse_container,
                build_clickhouse_statements(
                    rows,
                    database=database,
                    replace_sales=args.replace_sales,
                ),
            )
            print(f"ClickHouse demo data seeded: rows={len(rows)} months={args.months}")
        if args.oracle_sysdba:
            oracle_home = _resolve_oracle_home(args.oracle_home)
            _run_oracle_sysdba(
                sqlplus=oracle_home / "bin" / "sqlplus.exe",
                pdb=args.oracle_pdb,
                merge_sql=build_oracle_merge(
                    rows,
                    schema_owner=args.oracle_schema_owner,
                    replace_sales=args.replace_sales,
                ),
            )
            print(f"Oracle demo data seeded: rows={len(rows)} months={args.months}")
    except Exception as exc:
        print(f"Demo data seeding failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
