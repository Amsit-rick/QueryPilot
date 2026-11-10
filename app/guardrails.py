"""Deterministic SQL safety boundary shared by every query entry point."""

from __future__ import annotations

import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope


class QueryError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# SQLGlot normalizes dialect-specific functions (for example DATE_FORMAT to
# TIME_TO_STR). Unknown functions are denied because database extensions can
# perform file, network, sleep or stored-procedure operations even in SELECT.
SAFE_QUERY_FUNCTIONS = {
    "ABS", "AVG", "CASE", "CAST", "CEIL", "COALESCE", "CONCAT", "COUNT",
    "CURRENT_DATE", "CURRENT_DATETIME", "CURRENT_TIME", "CURRENT_TIMESTAMP",
    "DATE", "DATE_ADD", "DATE_DIFF", "DATE_SUB", "DAY", "DENSE_RANK",
    "EXTRACT", "FLOOR", "GREATEST", "IF", "IFNULL", "LAG", "LEAD",
    "LEAST", "LENGTH", "LOWER", "MAX", "MIN", "MONTH", "NULLIF",
    "RANK", "ROUND", "ROW_NUMBER", "STR_TO_TIME", "SUBSTRING", "SUM",
    "TIME_TO_STR", "TRIM", "UPPER", "YEAR",
    # MySQL's FIELD() only maps a value to its position in a literal list. It
    # is commonly used to order CASE buckets and performs no I/O.
    "FIELD",
    # SQLGlot rewrites MONTH(date_col) / DAY(date_col) as an explicit cast to
    # DATE (TsOrDsToDate). It is a pure value conversion with no I/O, so it must
    # stay allowed; without it the common "monthly trend" question is rejected.
    "TS_OR_DS_TO_DATE",
    # Pure date/time formatting and bucketing functions normalized by SQLGlot
    # across the supported database dialects. These perform no file, network,
    # session or stored-procedure operations and are safe inside a read-only
    # SELECT. Unknown functions remain denied by default.
    "TS_OR_DS_TO_TIMESTAMP",  # SQLite strftime input normalization
    "TIMESTAMP_TRUNC",        # PostgreSQL date_trunc
    "DATE_TRUNC",             # Oracle TRUNC(date, unit)
    "TO_CHAR",                # Oracle date formatting
    "DATE_FROM_PARTS",        # SQL Server DATEFROMPARTS
    "TOSTARTOFMONTH",         # ClickHouse toStartOfMonth
}

# Kept as an early, explicit diagnostic before parsing. The function allowlist
# below is the authoritative rule and also catches casing/format variations.
BLOCKED_FUNCTIONS = {
    "benchmark", "dblink", "http_get", "load_extension", "load_file",
    "pg_ls_dir", "pg_read_binary_file", "pg_read_file", "readfile",
    "sleep", "sys_eval", "sys_exec", "writefile", "fts3_tokenizer",
}

_DIALECTS = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
    "oracle": "oracle",
    "sqlserver": "tsql",
    "tsql": "tsql",
    "clickhouse": "clickhouse",
}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validation_dialect(backend: str | None) -> str:
    """Return the SQLGlot dialect used by the canonical policy pipeline."""
    return _DIALECTS.get(str(backend or "sqlite").lower(), "sqlite")


def query_function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return str(function.name or "").upper()
    return str(function.sql_name() or "").upper()


def is_safe_query_function(function: exp.Func) -> bool:
    """Return whether a parsed SQL function is explicitly safe to execute."""
    if isinstance(function, exp.Connector):
        return True
    name = query_function_name(function)
    return bool(name) and name.casefold() not in BLOCKED_FUNCTIONS and name in SAFE_QUERY_FUNCTIONS


def validate_sql(
    sql: str,
    schema: dict[str, set[str]],
    max_length: int = 5000,
    *,
    dialect: str = "sqlite",
    max_params: int = 50,
) -> str:
    """Allow one bounded read-only SELECT over the authorized schema.

    The caller must still use a database read-only account and bind values as
    driver parameters. This function protects SQL structure and identifiers;
    it intentionally never treats the LLM or a prompt as a security boundary.
    """
    normalized = sql.strip().rstrip(";").strip()
    lowered = normalized.casefold()
    if any(re.search(rf"\b{re.escape(name)}\s*\(", lowered) for name in BLOCKED_FUNCTIONS):
        raise QueryError("E_READONLY_VIOLATION", "检测到禁止调用的函数")
    if not normalized or len(normalized) > max_length:
        raise QueryError("E_QUERY_LIMIT", "SQL 为空或超过长度限制")
    read_dialect = validation_dialect(dialect)
    try:
        statements = parse(normalized, read=read_dialect)
    except (ParseError, SqlglotError) as exc:
        raise QueryError("E_QUERY_INVALID", "SQL 语法无效") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise QueryError("E_READONLY_VIOLATION", "仅允许执行单条 SELECT 语句")
    tree = statements[0]

    # SELECT can still contain locking, export, commands or DML nodes.
    if any(tree.find_all(exp.Lock, exp.Into, exp.Command, exp.DDL, exp.DML)):
        raise QueryError("E_READONLY_VIOLATION", "SELECT 包含锁、导出或写入操作")
    if any(
        select.args.get(key)
        for select in tree.find_all(exp.Select)
        for key in ("settings", "format", "options", "operation_modifiers", "locks")
    ):
        raise QueryError("E_READONLY_VIOLATION", "禁止覆盖查询执行设置或申请数据库锁")
    if any(tree.find_all(exp.Union, exp.Intersect, exp.Except)):
        raise QueryError("E_READONLY_VIOLATION", "集合查询需要单独审批，当前未放行")

    with_clause = tree.args.get("with")
    if with_clause and with_clause.args.get("recursive"):
        raise QueryError("E_READONLY_VIOLATION", "禁止递归 CTE")
    ctes = list(tree.find_all(exp.CTE))
    if len(ctes) > 8:
        raise QueryError("E_QUERY_LIMIT", "CTE 数量超过限制")

    placeholders = list(tree.find_all(exp.Placeholder))
    if len(placeholders) > max_params:
        raise QueryError("E_QUERY_LIMIT", "SQL 参数数量超过限制")
    for placeholder in placeholders:
        if not _IDENTIFIER.fullmatch(str(placeholder.this or "")):
            raise QueryError("E_QUERY_INVALID", "仅允许命名参数")
    if any(tree.find_all(exp.Parameter)):
        raise QueryError("E_QUERY_INVALID", "仅允许 :name 格式的命名参数")

    known = {name.lower(): {column.lower() for column in columns} for name, columns in schema.items()}
    cte_names: set[str] = set()
    for cte in ctes:
        name = cte.alias_or_name.lower()
        if not name or name in known or name in cte_names or not isinstance(cte.this, exp.Select):
            raise QueryError("E_QUERY_INVALID", "CTE 名称无效、重复或覆盖业务表")
        projected = cte.alias_column_names or cte.this.named_selects
        columns = {str(column).lower() for column in projected if str(column).strip()}
        if not columns or len(columns) != len(projected) or "*" in columns:
            raise QueryError("E_QUERY_INVALID", "CTE 必须声明唯一且明确的输出列")
        cte_names.add(name)
        known[name] = columns
    for cte in ctes:
        name = cte.alias_or_name.lower()
        if any(table.name.lower() == name for table in cte.this.find_all(exp.Table)):
            raise QueryError("E_READONLY_VIOLATION", "禁止 CTE 自引用")
    physical_tables = 0
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog or not isinstance(table.this, exp.Identifier):
            raise QueryError("E_READONLY_VIOLATION", "禁止跨库、跨 Schema 或表函数查询")
        table_name = table.name.lower()
        if table_name not in known:
            raise QueryError("E_HALLUCINATED_FIELD", f"未授权或不存在的表：{table.name}")
        if table_name not in cte_names:
            physical_tables += 1
    if not physical_tables:
        raise QueryError("E_HALLUCINATED_FIELD", "查询必须引用已授权业务表")
    if any(join.args.get("on") is None or join.args.get("on") == exp.true() for join in tree.find_all(exp.Join)):
        raise QueryError("E_CARTESIAN_JOIN", "JOIN 必须包含 ON 或 USING 条件")

    for function in tree.find_all(exp.Func):
        name = query_function_name(function)
        if not is_safe_query_function(function):
            raise QueryError("E_READONLY_VIOLATION", f"函数未在安全白名单中：{name or 'UNKNOWN'}")

    def source_columns(source: exp.Expression | Scope) -> set[str]:
        if isinstance(source, exp.Table):
            return known.get(source.name.lower(), set())
        projected = source.outer_columns or source.expression.named_selects
        return {str(column).lower() for column in projected}

    for scope in traverse_scope(tree):
        selected = {alias.lower(): source for alias, (_, source) in scope.selected_sources.items()}
        select_aliases = {
            expression.alias_or_name.lower()
            for expression in scope.expression.expressions
            if isinstance(expression, exp.Alias)
        }
        for column in scope.columns:
            name = column.name.lower()
            qualifier = column.table.lower() if column.table else ""
            if qualifier:
                source = selected.get(qualifier)
                if source is None or name not in source_columns(source):
                    raise QueryError("E_HALLUCINATED_FIELD", f"未授权或不存在的字段：{column.sql()}")
                continue
            if name in select_aliases:
                continue
            matches = sum(name in source_columns(source) for source in selected.values())
            if matches == 0:
                raise QueryError("E_HALLUCINATED_FIELD", f"未授权或不存在的字段：{column.name}")
            if matches > 1:
                raise QueryError("E_AMBIGUOUS_FIELD", f"多表查询中的重复字段必须使用表别名：{column.name}")
    return normalized


def bound_query_rows(sql: str, *, dialect: str, max_rows: int, offset: int = 0) -> str:
    """Return SQL with a literal execution bound used before DB execution.

    One extra row is requested so the caller can report ``truncated=true``.
    The external offset is included because legacy executors skip those rows
    after execution instead of rendering it into the SQL statement.
    """
    if max_rows < 1 or offset < 0:
        raise QueryError("E_QUERY_LIMIT", "查询行数或偏移量无效")
    read_dialect = validation_dialect(dialect)
    try:
        tree = parse(sql, read=read_dialect)[0]
    except (IndexError, ParseError, SqlglotError) as exc:
        raise QueryError("E_QUERY_INVALID", "SQL 语法无效") from exc
    bound = offset + max_rows + 1
    existing = tree.args.get("limit")
    if existing is not None:
        value = existing.expression
        if not isinstance(value, exp.Literal) or not value.is_int or existing.args.get("limit_options"):
            raise QueryError("E_QUERY_LIMIT", "LIMIT 必须使用非负整数字面量")
        current = int(value.this)
        if current < 0:
            raise QueryError("E_QUERY_LIMIT", "LIMIT 不能为负数")
        bound = min(bound, current)
    return tree.limit(bound).sql(dialect=read_dialect, comments=False)
