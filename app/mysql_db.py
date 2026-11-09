import pymysql
import re
import threading
import time
from contextlib import contextmanager
from sqlglot import Dialect, exp, parse_one
from .config import settings
from .guardrails import QueryError

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def distinct_values(table: str, column: str, limit: int = 16) -> list:
    """Sample stored enumeration values for the grounding context.

    Identifiers are validated and quoted; the query runs on the read-only
    business connection. Callers must only pass columns that are already in the
    caller's authorized schema and are not row-scoped.
    """
    for name in (table, column):
        if not _IDENTIFIER.match(str(name)):
            raise ValueError("invalid identifier for value sampling")
    limit = max(1, min(int(limit), 100))
    with query_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT `{table}`.`{column}` FROM `{table}` "
                f"WHERE `{table}`.`{column}` IS NOT NULL LIMIT {limit}"
            )
            return [row[0] for row in cur.fetchall()]


class _ConnectionPool:
    """Bounded, thread-safe pool of reusable PyMySQL connections.

    Previously every request opened a new TCP connection and authenticated
    again, which is the first bottleneck under concurrency. Connections are
    validated with ``ping(reconnect=False)`` on checkout and dropped when they
    are dead, closed by the caller, or older than the idle TTL.
    """

    def __init__(self, factory, *, size, timeout, idle_ttl, exhausted_message, clock=time.monotonic):
        self._factory = factory
        self._timeout = max(0.0, float(timeout))
        self._idle_ttl = max(0.0, float(idle_ttl))
        self._clock = clock
        self._exhausted_message = exhausted_message
        self._lock = threading.Lock()
        self._idle: list[tuple[object, float]] = []
        self._slots = threading.BoundedSemaphore(max(1, int(size)))

    @staticmethod
    def _close(conn) -> None:
        try:
            conn.close()
        except Exception:
            pass

    def _take(self):
        while True:
            with self._lock:
                entry = self._idle.pop() if self._idle else None
            if entry is None:
                return self._factory()
            conn, used_at = entry
            if not getattr(conn, "open", True) or self._clock() - used_at > self._idle_ttl:
                self._close(conn)
                continue
            try:
                conn.ping(reconnect=False)
            except Exception:
                self._close(conn)
                continue
            return conn

    def _put(self, conn) -> None:
        if not getattr(conn, "open", True):
            self._close(conn)
            return
        try:
            # PyMySQL starts a transaction even for plain SELECT statements
            # when autocommit is disabled. End it before pooling the connection
            # so the next request cannot inherit a repeatable-read snapshot or
            # uncommitted writes from the previous borrower.
            conn.rollback()
        except Exception:
            self._close(conn)
            return
        with self._lock:
            self._idle.append((conn, self._clock()))

    @contextmanager
    def acquire(self):
        if not self._slots.acquire(timeout=self._timeout):
            raise TimeoutError(self._exhausted_message)
        conn = None
        try:
            conn = self._take()
            yield conn
        except BaseException:
            if conn is not None:
                self._close(conn)
            raise
        else:
            self._put(conn)
        finally:
            self._slots.release()

    def close_all(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for conn, _ in idle:
            self._close(conn)

    @property
    def idle_count(self) -> int:
        with self._lock:
            return len(self._idle)


def _new_control_connection():
    return pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user, password=settings.mysql_password, database=settings.mysql_database, charset="utf8mb4", init_command="SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci", cursorclass=pymysql.cursors.Cursor, connect_timeout=int(settings.mysql_connect_timeout_seconds), read_timeout=int(settings.model_timeout_seconds), write_timeout=int(settings.model_timeout_seconds))


def _new_query_connection():
    """Open the business datasource with the dedicated read-only identity.

    Empty query credentials retain a development-only compatibility fallback;
    authenticated production configuration rejects that fallback at startup.
    """
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_query_user or settings.mysql_user,
        password=settings.mysql_query_password or settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        init_command="SET SESSION TRANSACTION READ ONLY",
        cursorclass=pymysql.cursors.Cursor,
        connect_timeout=int(settings.mysql_connect_timeout_seconds),
        read_timeout=int(settings.model_timeout_seconds),
        write_timeout=int(settings.model_timeout_seconds),
    )


_control_pool = _ConnectionPool(
    _new_control_connection,
    size=settings.mysql_max_connections,
    timeout=settings.mysql_connect_timeout_seconds,
    idle_ttl=settings.mysql_pool_idle_ttl_seconds,
    exhausted_message="MySQL connection limit reached",
)
_query_pool = _ConnectionPool(
    _new_query_connection,
    size=settings.mysql_max_connections,
    timeout=settings.mysql_connect_timeout_seconds,
    idle_ttl=settings.mysql_pool_idle_ttl_seconds,
    exhausted_message="MySQL query connection limit reached",
)


def reset_mysql_pools_for_tests() -> None:
    """Drop idle connections so tests see a clean pool."""
    _control_pool.close_all()
    _query_pool.close_all()


@contextmanager
def connection():
    with _control_pool.acquire() as conn:
        yield conn


def connect():
    return connection()


@contextmanager
def query_connection():
    with _query_pool.acquire() as conn:
        yield conn



def _compile_mysql(sql: str, params: dict) -> tuple[str, dict]:
    tree = parse_one(sql, read="mysql")
    base = Dialect.get_or_raise("mysql").generator_class

    class BoundGenerator(base):
        def placeholder_sql(self, expression):
            name = str(expression.this or "")
            if name not in params:
                raise QueryError("E_QUERY_INVALID", f"缺少查询参数：{name}")
            return f"%({name})s"

        def literal_sql(self, expression):
            value = super().literal_sql(expression)
            return value.replace("%", "%%") if params else value

    rendered = BoundGenerator(dialect="mysql", comments=False).generate(tree)
    names = {str(node.this) for node in tree.find_all(exp.Placeholder)}
    return rendered, {name: params[name] for name in names}

def mysql_schema():
    with query_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s ORDER BY TABLE_NAME, ORDINAL_POSITION", (settings.mysql_database,))
            result = {}
            for table, column in cur.fetchall(): result.setdefault(table, set()).add(column)
            return result


def mysql_schema_details():
    with query_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_KEY FROM information_schema.columns WHERE table_schema=%s ORDER BY TABLE_NAME, ORDINAL_POSITION", (settings.mysql_database,))
            tables = {}
            for table, column, key in cur.fetchall():
                item = tables.setdefault(table, {"columns": [], "primary_key": []})
                item["columns"].append(column)
                if key == "PRI": item["primary_key"].append(column)
            cur.execute("""
                SELECT kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION, kcu.COLUMN_NAME, kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME
                FROM information_schema.key_column_usage kcu
                JOIN information_schema.table_constraints tc
                  ON tc.CONSTRAINT_SCHEMA=kcu.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME=kcu.CONSTRAINT_NAME AND tc.TABLE_NAME=kcu.TABLE_NAME
                WHERE kcu.CONSTRAINT_SCHEMA=%s AND tc.CONSTRAINT_TYPE='FOREIGN KEY'
                ORDER BY kcu.TABLE_NAME, kcu.CONSTRAINT_NAME, kcu.ORDINAL_POSITION
            """, (settings.mysql_database,))
            grouped = {}
            for table, constraint, ordinal, from_column, to_table, to_column in cur.fetchall():
                item = grouped.setdefault((table, constraint, to_table), {"from_table": table, "from_columns": [], "to_table": to_table, "to_columns": [], "cardinality": "many_to_one", "confidence": 1.0, "source": "database_metadata", "_pairs": []})
                item["_pairs"].append((ordinal, from_column, to_column))
            relationships = []
            for item in grouped.values():
                pairs = sorted(item.pop("_pairs")); item["from_columns"] = [p[1] for p in pairs]; item["to_columns"] = [p[2] for p in pairs]; relationships.append(item)
            return {"tables": tables, "relationships": relationships}

def execute_mysql(sql, params, max_rows, offset=0):
    sql = sql.replace("strftime('%Y-%m', sold_at)", "DATE_FORMAT(sold_at, '%Y-%m')")
    compiled_sql, bound_params = _compile_mysql(sql, params or {})
    with query_connection() as conn:
        with conn.cursor() as cur:
            if bound_params:
                cur.execute(compiled_sql, bound_params)
            else:
                cur.execute(compiled_sql)
            columns = [item[0] for item in cur.description or []]
            if offset: cur.fetchmany(offset)
            rows = cur.fetchmany(max_rows + 1)
            return columns, [list(row) for row in rows[:max_rows]], len(rows) > max_rows
