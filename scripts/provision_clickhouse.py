"""Provision the local ClickHouse demo schema used by Query Pilot.

The operation is idempotent and intentionally requires an administrative
ClickHouse account. Query Pilot itself must continue using its SELECT-only
account from CLICKHOUSE_USER / CLICKHOUSE_PASSWORD.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENV_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if match and not match.group(1).startswith("#"):
            values[match.group(1)] = match.group(2).strip().strip("'\"")
    return values


def _setting(name: str, values: dict[str, str], default: str = "") -> str:
    return os.getenv(name, "").strip() or values.get(name, "").strip() or default


def _database_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("database must be a simple SQL identifier")
    return f"`{value}`"


def build_statements(*, database: str) -> list[str]:
    db = _database_identifier(database)
    return [
        f"CREATE DATABASE IF NOT EXISTS {db}",
        f"""CREATE TABLE IF NOT EXISTS {db}.customers (
            id UInt64,
            name String
        ) ENGINE = MergeTree ORDER BY id""",
        f"""CREATE TABLE IF NOT EXISTS {db}.sales (
            id UInt64,
            customer_id Nullable(UInt64),
            region String,
            amount Decimal(12, 2),
            sold_at Date
        ) ENGINE = MergeTree ORDER BY id""",
        f"ALTER TABLE {db}.sales ADD COLUMN IF NOT EXISTS sold_at Date DEFAULT toDate('2026-08-15')",
        f"ALTER TABLE {db}.sales UPDATE sold_at = addMonths(toDate('2026-08-15'), -toInt32((id - 1) % 6)) WHERE 1 SETTINGS mutations_sync = 2",
        f"INSERT INTO {db}.customers SELECT 1, '华东客户' WHERE NOT EXISTS (SELECT 1 FROM {db}.customers WHERE id = 1)",
        f"INSERT INTO {db}.customers SELECT 2, '华南客户' WHERE NOT EXISTS (SELECT 1 FROM {db}.customers WHERE id = 2)",
        f"INSERT INTO {db}.sales SELECT 1, 1, '华东', toDecimal64(1234.56, 2), toDate('2026-06-15') WHERE NOT EXISTS (SELECT 1 FROM {db}.sales WHERE id = 1)",
        f"INSERT INTO {db}.sales SELECT 2, 2, '华南', toDecimal64(987.65, 2), toDate('2026-07-15') WHERE NOT EXISTS (SELECT 1 FROM {db}.sales WHERE id = 2)",
        f"INSERT INTO {db}.sales SELECT 3, 1, '华东', toDecimal64(500.00, 2), toDate('2026-08-15') WHERE NOT EXISTS (SELECT 1 FROM {db}.sales WHERE id = 3)",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--container", help="run migrations through docker exec for a local container")
    args = parser.parse_args(argv)
    values = _read_env(args.env_file)
    host = _setting("CLICKHOUSE_HOST", values, "127.0.0.1")
    port = int(_setting("CLICKHOUSE_PORT", values, "9000"))
    database = _setting("CLICKHOUSE_DATABASE", values)
    admin_user = _setting("CLICKHOUSE_ADMIN_USER", values, "default")
    admin_password = _setting("CLICKHOUSE_ADMIN_PASSWORD", values)
    if not database:
        print("Missing configuration: CLICKHOUSE_DATABASE")
        return 2
    try:
        statements = build_statements(database=database)
    except ValueError as exc:
        print(f"Invalid configuration: {exc}")
        return 2
    if args.dry_run:
        for index, statement in enumerate(statements, start=1):
            print(f"-- {index}\n{statement}")
        return 0
    try:
        verification_sql = f"SELECT count(), countDistinct(toStartOfMonth(sold_at)) FROM {_database_identifier(database)}.sales"
        if args.container:
            if not _CONTAINER.fullmatch(args.container):
                raise ValueError("container must be a simple Docker container name")
            for statement in statements:
                subprocess.run(
                    ["docker", "exec", args.container, "clickhouse-client", "--query", statement],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            completed = subprocess.run(
                ["docker", "exec", args.container, "clickhouse-client", "--query", verification_sql],
                check=True,
                capture_output=True,
                text=True,
            )
            row_count, month_count = (int(value) for value in completed.stdout.strip().split())
        else:
            from clickhouse_driver import Client

            client = Client(host=host, port=port, user=admin_user, password=admin_password)
            for statement in statements:
                client.execute(statement)
            row_count, month_count = client.execute(verification_sql)[0]
    except Exception as exc:
        print(f"ClickHouse provisioning failed: {type(exc).__name__}")
        return 1
    print(f"ClickHouse demo schema provisioned: rows={row_count} monthly_buckets={month_count}")
    return 0 if row_count >= 3 and month_count >= 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
