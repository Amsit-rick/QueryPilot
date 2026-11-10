"""Conservative SQL boundary for experimental remote connectors."""
import re

from sqlglot import exp, parse
from sqlglot.dialects import Dialect
from sqlglot.errors import ErrorLevel, SqlglotError

from app.guardrails import QueryError, is_safe_query_function
from app.config import settings


REMOTE_DIALECTS = {"oracle": "oracle", "sqlserver": "tsql", "clickhouse": "clickhouse"}
def select_tree(sql, dialect):
    if not sql.strip() or len(sql) > settings.max_sql_length:
        raise QueryError("E_QUERY_LIMIT", "SQL is empty or exceeds the configured limit")
    try:
        statements = parse(sql, read=dialect)
    except SqlglotError as exc:
        raise QueryError("E_QUERY_INVALID", "Invalid connector SQL") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise QueryError("E_READONLY_VIOLATION", "Only one SELECT is allowed")
    tree = statements[0]
    if any(select.args.get(key) for select in tree.find_all(exp.Select) for key in ("settings", "format", "options", "operation_modifiers")):
        raise QueryError("E_READONLY_VIOLATION", "Query execution overrides are disabled")
    if any(tree.find_all(exp.Into, exp.Lock, exp.Dot, exp.Command, exp.DDL, exp.DML)):
        raise QueryError("E_READONLY_VIOLATION", "Unsafe SELECT operation")
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog or not isinstance(table.this, exp.Identifier):
            raise QueryError("E_READONLY_VIOLATION", "Qualified tables and table functions are disabled")
    for function in tree.find_all(exp.Func):
        if not is_safe_query_function(function):
            raise QueryError("E_READONLY_VIOLATION", "Function is not enabled for this connector")
    for placeholder in tree.find_all(exp.Placeholder):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(placeholder.this or "")):
            raise QueryError("E_QUERY_INVALID", "Use named parameters")
    if any(tree.find_all(exp.Parameter)):
        raise QueryError("E_QUERY_INVALID", "Use :name parameters")
    return tree


def compile_remote_sql(tree, backend, params):
    """Render bound parameters through SQLGlot, never by replacing user SQL."""
    dialect = REMOTE_DIALECTS[backend]
    pyformat = backend != "oracle"
    base = Dialect.get_or_raise(dialect).generator_class

    class BoundGenerator(base):
        def placeholder_sql(self, expression):
            name = expression.this
            if name not in params:
                raise QueryError("E_QUERY_INVALID", "Missing query parameter")
            return f"%({name})s" if pyformat else f":{name}"

        def literal_sql(self, expression):
            value = super().literal_sql(expression)
            if backend == "sqlserver" and "%(" in value:
                raise QueryError("E_QUERY_INVALID", "Driver placeholder syntax is not allowed in literals")
            return value.replace("%", "%%") if backend == "clickhouse" else value

        def identifier_sql(self, expression):
            value = super().identifier_sql(expression)
            if backend == "sqlserver" and "%(" in value:
                raise QueryError("E_QUERY_INVALID", "Driver placeholder syntax is not allowed in identifiers")
            return value.replace("%", "%%") if backend == "clickhouse" else value

        def mod_sql(self, expression):
            operator = "%%" if backend == "clickhouse" else "%"
            return self.binary(expression, operator)

    try:
        return BoundGenerator(dialect=dialect, comments=False, unsupported_level=ErrorLevel.RAISE).generate(tree)
    except SqlglotError as exc:
        raise QueryError("E_QUERY_INVALID", "SQL is unsupported by this connector") from exc
