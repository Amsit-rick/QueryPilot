"""Load generated enterprise demo tables from SQLite into MySQL.

Run after the MySQL container is healthy. Credentials are read from MYSQL_* env vars.
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from pathlib import Path
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings

TABLES = {
    "enterprise_customers": "id BIGINT PRIMARY KEY, customer_code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(128) NOT NULL, region VARCHAR(32) NOT NULL, industry VARCHAR(64) NOT NULL, `level` VARCHAR(8) NOT NULL, signup_date DATE NOT NULL, credit_limit DECIMAL(18,2) NOT NULL",
    "products": "id BIGINT PRIMARY KEY, sku VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(128) NOT NULL, category VARCHAR(64) NOT NULL, cost DECIMAL(18,2) NOT NULL, list_price DECIMAL(18,2) NOT NULL",
    "enterprise_orders": "id BIGINT PRIMARY KEY, order_no VARCHAR(32) UNIQUE NOT NULL, customer_id BIGINT NOT NULL, order_date DATE NOT NULL, region VARCHAR(32) NOT NULL, status VARCHAR(24) NOT NULL, channel VARCHAR(24) NOT NULL, total_amount DECIMAL(18,2) NOT NULL",
    "order_items": "id BIGINT PRIMARY KEY, order_id BIGINT NOT NULL, product_id BIGINT NOT NULL, quantity INT NOT NULL, unit_price DECIMAL(18,2) NOT NULL, discount DECIMAL(8,4) NOT NULL, line_amount DECIMAL(18,2) NOT NULL",
    "inventory": "id BIGINT PRIMARY KEY, product_id BIGINT NOT NULL, warehouse VARCHAR(64) NOT NULL, available_qty INT NOT NULL, reorder_point INT NOT NULL, snapshot_date DATE NOT NULL",
    "suppliers": "id BIGINT PRIMARY KEY, supplier_code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(128) NOT NULL, region VARCHAR(32) NOT NULL, rating DECIMAL(3,1) NOT NULL, active TINYINT NOT NULL",
    "employees": "id BIGINT PRIMARY KEY, employee_no VARCHAR(32) UNIQUE NOT NULL, department VARCHAR(64) NOT NULL, region VARCHAR(32) NOT NULL, hire_date DATE NOT NULL, monthly_cost DECIMAL(18,2) NOT NULL",
    "payments": "id BIGINT PRIMARY KEY, payment_no VARCHAR(32) UNIQUE NOT NULL, order_id BIGINT NOT NULL, paid_date DATE NOT NULL, amount DECIMAL(18,2) NOT NULL, method VARCHAR(24) NOT NULL, status VARCHAR(24) NOT NULL",
}


def configured_value(name: str, fallback: str = "") -> str:
    value = os.getenv(name, "").strip() or str(fallback).strip()
    if not value:
        raise RuntimeError(f"{name} must be set by the calling environment")
    return value


def required_env(name: str) -> str:
    """Backward-compatible strict helper used by deployment checks/tests."""
    return configured_value(name)


def load(sqlite_path: Path, *, if_empty: bool = False) -> bool:
    source = sqlite3.connect(sqlite_path)
    mysql = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "").strip() or settings.mysql_host,
        port=int(os.getenv("MYSQL_PORT", "").strip() or settings.mysql_port),
        user=os.getenv("MYSQL_USER", "").strip() or settings.mysql_user,
        password=configured_value("MYSQL_PASSWORD", settings.mysql_password),
        database=os.getenv("MYSQL_DATABASE", "").strip() or settings.mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with mysql.cursor() as cur:
            if if_empty:
                cur.execute("SHOW TABLES LIKE 'payments'")
                if cur.fetchone():
                    cur.execute("SELECT COUNT(*) FROM payments")
                    if int(cur.fetchone()[0] or 0) > 0:
                        print("Enterprise demo data already exists; skipping import.")
                        return False
            for table, definition in TABLES.items():
                columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
                if not columns:
                    raise RuntimeError(f"SQLite source table missing: {table}")
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
                cur.execute(f"CREATE TABLE `{table}` ({definition}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
                placeholders = ",".join(["%s"] * len(columns))
                sql = f"INSERT INTO `{table}` ({','.join('`'+c+'`' for c in columns)}) VALUES ({placeholders})"
                select_columns = ",".join('"' + c + '"' for c in columns)
                rows = source.execute(f'SELECT {select_columns} FROM "{table}"')
                count = 0
                while batch := rows.fetchmany(5000):
                    cur.executemany(sql, batch)
                    count += len(batch)
                print(f"{table}: {count} rows")
            cur.execute("CREATE INDEX idx_enterprise_orders_customer_date ON enterprise_orders(customer_id, order_date)")
            cur.execute("CREATE INDEX idx_order_items_order ON order_items(order_id)")
            cur.execute("CREATE INDEX idx_payments_order ON payments(order_id)")
        mysql.commit()
        return True
    except Exception:
        mysql.rollback()
        raise
    finally:
        source.close()
        mysql.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load enterprise demo tables from SQLite into MySQL")
    parser.add_argument("--sqlite-source", default=os.getenv("SQLITE_SOURCE", "data/demo.db"))
    parser.add_argument("--if-empty", action="store_true", help="skip import when MySQL payments already contains rows")
    args = parser.parse_args()
    loaded = load(Path(args.sqlite_source), if_empty=args.if_empty)
    print(f"Enterprise demo import complete: loaded={loaded}")
