"""Generate deterministic synthetic enterprise data for the local SQLite demo.

Usage (Windows CMD): python scripts\generate_enterprise_data.py --rows 500000
The command is repeatable and replaces only the generated enterprise tables.
"""
from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

REGIONS = ["华东", "华南", "华北", "西南", "西北", "东北"]
STATUSES = ["completed", "completed", "completed", "pending", "cancelled"]


def chunks(rows, size=5000):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def generate(path: Path, target_rows: int, seed: int = 20260912) -> None:
    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    customers = max(10_000, target_rows // 10)
    products = max(1_000, target_rows // 100)
    orders = max(20_000, target_rows // 2)
    items = max(orders * 2, target_rows)
    payments = max(orders // 2, target_rows // 3)
    with sqlite3.connect(path) as db:
        def bulk(sql, rows):
            for batch in chunks(rows):
                db.executemany(sql, batch)

        db.executescript("""
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS enterprise_customers (
          id INTEGER PRIMARY KEY, customer_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          region TEXT NOT NULL, industry TEXT NOT NULL, level TEXT NOT NULL,
          signup_date TEXT NOT NULL, credit_limit REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          category TEXT NOT NULL, cost REAL NOT NULL, list_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS enterprise_orders (
          id INTEGER PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, customer_id INTEGER NOT NULL,
          order_date TEXT NOT NULL, region TEXT NOT NULL, status TEXT NOT NULL,
          channel TEXT NOT NULL, total_amount REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items (
          id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
          quantity INTEGER NOT NULL, unit_price REAL NOT NULL, discount REAL NOT NULL,
          line_amount REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inventory (
          id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, warehouse TEXT NOT NULL,
          available_qty INTEGER NOT NULL, reorder_point INTEGER NOT NULL, snapshot_date TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS suppliers (
          id INTEGER PRIMARY KEY, supplier_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          region TEXT NOT NULL, rating REAL NOT NULL, active INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS employees (
          id INTEGER PRIMARY KEY, employee_no TEXT UNIQUE NOT NULL, department TEXT NOT NULL,
          region TEXT NOT NULL, hire_date TEXT NOT NULL, monthly_cost REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments (
          id INTEGER PRIMARY KEY, payment_no TEXT UNIQUE NOT NULL, order_id INTEGER NOT NULL,
          paid_date TEXT NOT NULL, amount REAL NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL
        );
        """)
        for table in ("enterprise_customers", "products", "enterprise_orders", "order_items", "inventory", "suppliers", "employees", "payments"):
            db.execute(f"DELETE FROM {table}")
        industries = ["制造业", "零售", "金融", "医疗", "教育", "互联网", "物流"]
        levels = ["A", "B", "C", "D"]
        bulk("INSERT INTO enterprise_customers VALUES (?,?,?,?,?,?,?,?)",
            ((i, f"C{i:07d}", f"企业客户{i:06d}", rng.choice(REGIONS), rng.choice(industries), rng.choice(levels),
             (date(2018, 1, 1) + timedelta(days=rng.randrange(3000))).isoformat(), round(rng.uniform(50_000, 5_000_000), 2))
            for i in range(1, customers + 1)))
        categories = ["办公设备", "软件服务", "工业零件", "仓储耗材", "数字服务"]
        bulk("INSERT INTO products VALUES (?,?,?,?,?,?)",
            ((i, f"SKU-{i:06d}", f"产品{i:05d}", rng.choice(categories), cost := round(rng.uniform(10, 5000), 2), round(cost * rng.uniform(1.15, 1.8), 2))
            for i in range(1, products + 1)))
        start = date(2025, 1, 1)
        bulk("INSERT INTO enterprise_orders VALUES (?,?,?,?,?,?,?,?)",
            ((i, f"SO-{2025}{i:08d}", customer := rng.randrange(1, customers + 1), d := (start + timedelta(days=rng.randrange(620))).isoformat(),
             REGIONS[(customer - 1) % len(REGIONS)], rng.choice(STATUSES), rng.choice(["online", "direct", "partner"]), round(rng.uniform(100, 250000), 2))
            for i in range(1, orders + 1)))
        bulk("INSERT INTO order_items VALUES (?,?,?,?,?,?,?)",
            ((i, order_id := rng.randrange(1, orders + 1), product := rng.randrange(1, products + 1), qty := rng.randrange(1, 30), price := round(rng.uniform(20, 5000), 2), discount := round(rng.choice([0, 0.05, 0.1, 0.15]), 2), round(qty * price * (1 - discount), 2))
            for i in range(1, items + 1)))
        warehouses = ["上海仓", "广州仓", "北京仓", "成都仓"]
        bulk("INSERT INTO inventory VALUES (?,?,?,?,?,?)",
            ((i, ((i - 1) % products) + 1, warehouses[(i - 1) % len(warehouses)], rng.randrange(0, 5000), rng.randrange(50, 500), "2026-09-01")
            for i in range(1, products * 4 + 1)))
        bulk("INSERT INTO suppliers VALUES (?,?,?,?,?,?)",
            ((i, f"SUP-{i:06d}", f"供应商{i:05d}", rng.choice(REGIONS), round(rng.uniform(2.5, 5), 1), int(rng.random() > .08))
            for i in range(1, max(1000, products // 10) + 1)))
        bulk("INSERT INTO employees VALUES (?,?,?,?,?,?)",
            ((i, f"E-{i:06d}", rng.choice(["销售", "财务", "运营", "研发", "供应链"]), rng.choice(REGIONS),
             (date(2015, 1, 1) + timedelta(days=rng.randrange(4000))).isoformat(), round(rng.uniform(6000, 60000), 2))
            for i in range(1, max(2000, customers // 50) + 1)))
        bulk("INSERT INTO payments VALUES (?,?,?,?,?,?,?)",
            ((i, f"PAY-{i:09d}", rng.randrange(1, orders + 1), (start + timedelta(days=rng.randrange(620))).isoformat(), round(rng.uniform(100, 200000), 2), rng.choice(["bank", "wechat", "alipay", "credit"]), rng.choice(["paid", "paid", "pending", "refunded"]))
            for i in range(1, payments + 1)))
        db.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_date ON enterprise_orders(customer_id, order_date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id)")
        db.commit()
        print(f"generated enterprise data in {path}: customers={customers}, products={products}, orders={orders}, items={items}, payments={payments}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/demo.db")
    parser.add_argument("--rows", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=20260912)
    args = parser.parse_args()
    generate(Path(args.db), args.rows, args.seed)
