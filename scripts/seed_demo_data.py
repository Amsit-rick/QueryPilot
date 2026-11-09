import argparse
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REGIONS = ["华东", "华南", "华北", "华中", "西南", "西北", "东北"]
INDUSTRIES = ["科技", "零售", "制造", "物流", "医疗", "教育", "能源"]
PREFIXES = ["星河", "远景", "华信", "云帆", "新锐", "恒达", "启明", "联合", "卓越", "东方"]


def build_data(customer_count=200, sale_count=5000, seed=20260830):
    rng = random.Random(seed)
    customers = [(i, f"{rng.choice(PREFIXES)}{rng.choice(INDUSTRIES)}{i:03d}", rng.choice(REGIONS)) for i in range(1, customer_count + 1)]
    start = date(2025, 1, 1)
    sales = []
    for i in range(1, sale_count + 1):
        sold_at = start + timedelta(days=rng.randrange(0, 730))
        sales.append((i, rng.choice(REGIONS), round(rng.uniform(80, 50000), 2), sold_at.isoformat()))
    return customers, sales


def seed_sqlite(path, customers, sales):
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM sales"); conn.execute("DELETE FROM customers")
        conn.executemany("INSERT INTO customers(id,name,region) VALUES(?,?,?)", customers)
        conn.executemany("INSERT INTO sales(id,region,amount,sold_at) VALUES(?,?,?,?)", sales)


def seed_mysql(customers, sales, if_empty=False):
    from app.mysql_db import connect
    with connect() as conn:
        with conn.cursor() as cur:
            if if_empty:
                cur.execute("SELECT COUNT(*) FROM sales")
                if int(cur.fetchone()[0] or 0) > 0:
                    return False
            cur.execute("DELETE FROM sales"); cur.execute("DELETE FROM customers")
            cur.executemany("INSERT INTO customers(id,name,region) VALUES(%s,%s,%s)", customers)
            cur.executemany("INSERT INTO sales(id,region,amount,sold_at) VALUES(%s,%s,%s,%s)", sales)
        conn.commit()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["sqlite", "mysql", "both"], default="both")
    parser.add_argument("--customers", type=int, default=200)
    parser.add_argument("--sales", type=int, default=5000)
    parser.add_argument("--if-empty", action="store_true", help="只在 MySQL sales 表为空时写入演示数据")
    args = parser.parse_args()
    customer_rows, sale_rows = build_data(args.customers, args.sales)
    if args.backend in {"sqlite", "both"}: seed_sqlite("./data/demo.db", customer_rows, sale_rows)
    seeded_mysql = None
    if args.backend in {"mysql", "both"}: seeded_mysql = seed_mysql(customer_rows, sale_rows, args.if_empty)
    print(f"Seed complete: customers={len(customer_rows)}, sales={len(sale_rows)}, backend={args.backend}, mysql_seeded={seeded_mysql}")
