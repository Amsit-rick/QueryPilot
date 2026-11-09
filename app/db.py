import sqlite3
import time
from pathlib import Path


def init_db(path: str) -> None:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        existing = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "sales" not in existing:
            conn.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, region TEXT NOT NULL, amount REAL NOT NULL, sold_at TEXT NOT NULL)")
            conn.executemany("INSERT INTO sales(id, region, amount, sold_at) VALUES (?, ?, ?, ?)", [(1, "华东", 1200.0, "2026-08-01"), (2, "华南", 980.0, "2026-08-02"), (3, "华东", 760.0, "2026-08-03")])
        if "customers" not in existing:
            conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL)")
            conn.executemany("INSERT INTO customers(id, name, region) VALUES (?, ?, ?)", [(1, "星河科技", "华东"), (2, "南方零售", "华南")])


def schema(path: str) -> dict[str, set[str]]:
    init_db(path)
    with sqlite3.connect(path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        return {name: {row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')} for (name,) in tables}


def schema_details(path: str) -> dict:
    init_db(path)
    with sqlite3.connect(path) as conn:
        tables = {}
        for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            columns = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            tables[name] = {
                "columns": [row[1] for row in columns],
                "primary_key": [row[1] for row in sorted(columns, key=lambda value: value[5]) if row[5]],
            }
        grouped = {}
        for table in tables:
            for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                key = (table, row[0], row[2])
                item = grouped.setdefault(key, {"from_table": table, "from_columns": [], "to_table": row[2], "to_columns": [], "cardinality": "many_to_one", "confidence": 1.0, "source": "database_metadata", "_seq": []})
                item["_seq"].append((row[1], row[3], row[4]))
        relationships = []
        for item in grouped.values():
            pairs = sorted(item.pop("_seq"))
            item["from_columns"] = [pair[1] for pair in pairs]
            item["to_columns"] = [pair[2] for pair in pairs]
            relationships.append(item)
        return {"tables": tables, "relationships": relationships}


def execute_readonly(path: str, sql: str, max_rows: int, params: dict[str, object] | None = None, timeout_seconds: float = 30.0, offset: int = 0) -> tuple[list[str], list[list[object]], bool]:
    init_db(path)
    with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as conn:
        deadline = time.monotonic() + timeout_seconds
        conn.set_progress_handler(lambda: 1 if time.monotonic() >= deadline else 0, 1000)
        try:
            cur = conn.execute(sql, params or {})
        except sqlite3.OperationalError as exc:
            message = str(exc)
            if "interrupted" in message:
                raise ValueError("E_TIMEOUT:查询超过时间限制") from exc
            if "no such column" in message or "no such table" in message:
                raise ValueError("E_HALLUCINATED_FIELD:" + message) from exc
            raise ValueError("E_QUERY_ERROR:" + message) from exc
        columns = [d[0] for d in cur.description or []]
        if offset:
            cur.fetchmany(offset)
        rows = cur.fetchmany(max_rows + 1)
        return columns, [list(row) for row in rows[:max_rows]], len(rows) > max_rows
