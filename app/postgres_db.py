"""Read-only PostgreSQL adapter. Credentials are supplied via POSTGRES_DSN."""
from sqlglot import Dialect, exp, parse_one

from .config import settings
from .guardrails import QueryError

def _connect():
    dsn = settings.postgres_readonly_dsn or settings.postgres_dsn
    if not dsn:
        raise RuntimeError("未配置 POSTGRES_READONLY_DSN 或 POSTGRES_DSN")
    import psycopg
    return psycopg.connect(dsn, connect_timeout=int(settings.mysql_connect_timeout_seconds), options="-c default_transaction_read_only=on")


def _compile_postgres(sql: str, params: dict) -> tuple[str, dict]:
    tree = parse_one(sql, read="postgres")
    base = Dialect.get_or_raise("postgres").generator_class

    class BoundGenerator(base):
        def placeholder_sql(self, expression):
            name = str(expression.this or "")
            if name not in params:
                raise QueryError("E_QUERY_INVALID", f"缺少查询参数：{name}")
            return f"%({name})s"

        def literal_sql(self, expression):
            value = super().literal_sql(expression)
            return value.replace("%", "%%") if params else value

    rendered = BoundGenerator(dialect="postgres", comments=False).generate(tree)
    names = {str(node.this) for node in tree.find_all(exp.Placeholder)}
    return rendered, {name: params[name] for name in names}

def postgres_schema():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name, ordinal_position")
        result = {}
        for table, column in cur.fetchall(): result.setdefault(table, set()).add(column)
        return result


def postgres_schema_details():
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.table_name, c.column_name,
                   CASE WHEN tc.constraint_type='PRIMARY KEY' THEN 1 ELSE 0 END AS is_primary
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
              ON kcu.table_schema=c.table_schema AND kcu.table_name=c.table_name AND kcu.column_name=c.column_name
            LEFT JOIN information_schema.table_constraints tc
              ON tc.constraint_schema=kcu.constraint_schema AND tc.constraint_name=kcu.constraint_name
            WHERE c.table_schema='public' ORDER BY c.table_name, c.ordinal_position
        """)
        tables = {}
        for table, column, is_primary in cur.fetchall():
            item = tables.setdefault(table, {"columns": [], "primary_key": []})
            item["columns"].append(column)
            if is_primary: item["primary_key"].append(column)
        cur.execute("""
            SELECT kcu.table_name, kcu.constraint_name, kcu.ordinal_position, kcu.column_name, ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.constraint_schema=tc.constraint_schema
            WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
            ORDER BY kcu.table_name, kcu.constraint_name, kcu.ordinal_position
        """)
        grouped = {}
        for table, constraint, ordinal, from_column, to_table, to_column in cur.fetchall():
            item = grouped.setdefault((table, constraint, to_table), {"from_table": table, "from_columns": [], "to_table": to_table, "to_columns": [], "cardinality": "many_to_one", "confidence": 1.0, "source": "database_metadata", "_pairs": []})
            item["_pairs"].append((ordinal, from_column, to_column))
        relationships = []
        for item in grouped.values():
            pairs = sorted(item.pop("_pairs")); item["from_columns"] = [p[1] for p in pairs]; item["to_columns"] = [p[2] for p in pairs]; relationships.append(item)
        return {"tables": tables, "relationships": relationships}

def execute_postgres(sql, params, max_rows, offset=0):
    compiled_sql, bound_params = _compile_postgres(sql, params or {})
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(compiled_sql, bound_params or None)
        columns = [d.name for d in cur.description]
        if offset:
            cur.fetchmany(offset)
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        return columns, [list(r) for r in rows[:max_rows]], truncated

def test_postgres():
    try:
        with _connect() as conn, conn.cursor() as cur: cur.execute("SELECT 1")
        return {"ok": True, "backend": "postgres", "message": "连接成功"}
    except Exception as exc:
        return {"ok": False, "backend": "postgres", "message": str(exc)}
