"""Provision the SQL Server acceptance database and its read-only login.

The gateway account must be SELECT-only. This script needs a separate admin
login (sa by default) to create the database, the demo tables and the read-only
login, then verifies that the read-only account can read but cannot write.

Usage (Windows CMD, project root):

    .venv\\Scripts\\python.exe scripts\\provision_sqlserver.py --dry-run
    .venv\\Scripts\\python.exe scripts\\provision_sqlserver.py

Environment (from .env or the process):
    SQLSERVER_HOST, SQLSERVER_PORT, SQLSERVER_DATABASE
    SQLSERVER_ADMIN_USER (default sa), SQLSERVER_ADMIN_PASSWORD
    SQLSERVER_USER, SQLSERVER_PASSWORD   # the SELECT-only login to create
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
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


def _setting(name: str, file_values: dict[str, str], default: str = "") -> str:
    return os.getenv(name, "").strip() or file_values.get(name, "").strip() or default


def _quote_literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def _validate_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER.match(value):
        raise ValueError(f"{label} must be a simple SQL identifier")
    return value


def build_statements(
    *,
    database: str,
    readonly_login: str,
    readonly_password: str,
) -> list[str]:
    """Return the ordered provisioning statements. No connection is opened."""
    database = _validate_identifier(database, "database")
    readonly_login = _validate_identifier(readonly_login, "readonly login")
    if len(readonly_password) < 8 or "\n" in readonly_password or "\r" in readonly_password:
        raise ValueError("readonly password must be at least 8 characters on one line")
    login_literal = _quote_literal(readonly_password)
    return [
        f"IF DB_ID(N'{database}') IS NULL CREATE DATABASE [{database}];",
        f"""IF OBJECT_ID(N'{database}.dbo.customers', N'U') IS NULL
            CREATE TABLE [{database}].dbo.customers (
                id INT NOT NULL PRIMARY KEY,
                name NVARCHAR(100) NOT NULL
            );""",
        f"""IF OBJECT_ID(N'{database}.dbo.sales', N'U') IS NULL
            CREATE TABLE [{database}].dbo.sales (
                id INT NOT NULL PRIMARY KEY,
                customer_id INT NULL,
                region NVARCHAR(50) NOT NULL,
                amount DECIMAL(12,2) NOT NULL,
                sold_at DATE NOT NULL,
                CONSTRAINT fk_sales_customer FOREIGN KEY (customer_id)
                    REFERENCES [{database}].dbo.customers(id)
            );""",
        f"""IF COL_LENGTH(N'{database}.dbo.sales', N'sold_at') IS NULL
            ALTER TABLE [{database}].dbo.sales ADD sold_at DATE NULL;""",
        f"""UPDATE [{database}].dbo.sales
            SET sold_at = DATEADD(month, -((id - 1) % 6), CAST('2026-08-15' AS date))
            WHERE sold_at IS NULL;""",
        f"""IF EXISTS (
                SELECT 1 FROM [{database}].sys.columns
                WHERE object_id = OBJECT_ID(N'{database}.dbo.sales')
                  AND name = N'sold_at' AND is_nullable = 1
            ) ALTER TABLE [{database}].dbo.sales ALTER COLUMN sold_at DATE NOT NULL;""",
        f"""IF NOT EXISTS (SELECT 1 FROM [{database}].dbo.customers)
            INSERT INTO [{database}].dbo.customers (id, name)
            VALUES (1, N'\u534e\u4e1c\u5ba2\u6237'), (2, N'\u534e\u5357\u5ba2\u6237');""",
        f"""IF NOT EXISTS (SELECT 1 FROM [{database}].dbo.sales)
            INSERT INTO [{database}].dbo.sales (id, customer_id, region, amount, sold_at)
            VALUES (1, 1, N'\u534e\u4e1c', 1234.56, '2026-06-15'),
                   (2, 2, N'\u534e\u5357', 987.65, '2026-07-15'),
                   (3, 1, N'\u534e\u4e1c', 500.00, '2026-08-15');""",
        f"""IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'{readonly_login}')
            CREATE LOGIN [{readonly_login}] WITH PASSWORD = {login_literal}, CHECK_POLICY = OFF;""",
        f"""USE [{database}];
            IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'{readonly_login}')
                CREATE USER [{readonly_login}] FOR LOGIN [{readonly_login}];
            GRANT SELECT ON SCHEMA::dbo TO [{readonly_login}];""",
    ]


def _connect(**kwargs):
    import pymssql

    return pymssql.connect(**kwargs)


def _verify_readonly(*, host: str, port: int, database: str, user: str, password: str) -> dict[str, object]:
    connection = _connect(server=host, port=str(port), user=user, password=password, database=database, login_timeout=10)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM dbo.sales")
        rows = cursor.fetchone()[0]
        write_blocked = False
        try:
            cursor.execute("UPDATE dbo.sales SET amount = amount WHERE 1 = 0")
            connection.commit()
        except Exception as exc:
            write_blocked = True
            code = getattr(exc.args[0] if exc.args else None, "number", None)
            return {"rows": int(rows), "write_blocked": write_blocked, "write_error": code}
        return {"rows": int(rows), "write_blocked": write_blocked, "write_error": None}
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--dry-run", action="store_true", help="print statements without connecting")
    args = parser.parse_args(argv)
    file_values = _read_env(args.env_file)
    host = _setting("SQLSERVER_HOST", file_values)
    port = int(_setting("SQLSERVER_PORT", file_values, "1433"))
    database = _setting("SQLSERVER_DATABASE", file_values)
    readonly_user = _setting("SQLSERVER_USER", file_values)
    readonly_password = _setting("SQLSERVER_PASSWORD", file_values)
    admin_user = _setting("SQLSERVER_ADMIN_USER", file_values, "sa")
    admin_password = _setting("SQLSERVER_ADMIN_PASSWORD", file_values)
    missing = [
        name
        for name, value in {
            "SQLSERVER_HOST": host,
            "SQLSERVER_DATABASE": database,
            "SQLSERVER_USER": readonly_user,
            "SQLSERVER_PASSWORD": readonly_password,
            "SQLSERVER_ADMIN_PASSWORD": admin_password,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing configuration: {', '.join(missing)}")
        return 2
    try:
        statements = build_statements(
            database=database,
            readonly_login=readonly_user,
            readonly_password=readonly_password,
        )
    except ValueError as exc:
        print(f"Invalid configuration: {exc}")
        return 2
    if args.dry_run:
        for index, statement in enumerate(statements, start=1):
            print(f"-- {index}")
            print(statement.replace(readonly_password, "***"))
        return 0
    try:
        connection = _connect(server=host, port=str(port), user=admin_user, password=admin_password,
                              database="master", login_timeout=10)
    except Exception as exc:
        print(f"Admin connection failed: {type(exc).__name__}")
        return 2
    try:
        connection.autocommit(True)
        cursor = connection.cursor()
        for statement in statements:
            cursor.execute(statement)
    except Exception as exc:
        print(f"Provisioning failed: {type(exc).__name__}")
        return 1
    finally:
        connection.close()
    try:
        verification = _verify_readonly(host=host, port=port, database=database,
                                        user=readonly_user, password=readonly_password)
    except Exception as exc:
        print(f"Read-only verification failed: {type(exc).__name__}")
        return 1
    print(
        "SQL Server acceptance database provisioned: "
        f"rows={verification['rows']} write_blocked={verification['write_blocked']} "
        f"write_error={verification['write_error']}"
    )
    if verification["rows"] < 2:
        print("Seed data is missing; at least two sales rows are required.")
        return 1
    if not verification["write_blocked"]:
        print("The read-only account accepted a write; grant SELECT only.")
        return 1
    print("Next: python scripts/connector_acceptance.py --connector sqlserver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
