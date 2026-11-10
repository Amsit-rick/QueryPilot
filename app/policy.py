"""Configurable data-access policy: table / column / masking rules per role.

Policy is loaded from ``settings.policy_path`` (a versioned JSON file). When the
file is missing or invalid, a built-in default policy is used so local
development and tests keep working.

Rule semantics:
- ``tables``: ``"*"`` (all) or a list of table names a role may query.
- ``columns``: ``"*"`` (all) or ``{table: [column, ...]}``; restricted roles may
  not use ``SELECT *`` and must list allowed columns explicitly.
- ``mask``: ``{column: "mask" | "partial"}`` applied to returned rows.
- ``admin`` always has full access and no masking.
"""
from __future__ import annotations

import json
import re
import os
import tempfile
from pathlib import Path

import sqlglot
from sqlglot import exp

from .config import settings
from .guardrails import QueryError, validation_dialect


def _default_policy() -> dict:
    return {
        "version": "2026-09-03-v1",
        "datasources": {
            "default": {
                "roles": {
                    "admin": {"tables": "*", "columns": "*", "mask": {}},
                    "analyst": {"tables": "*", "columns": "*", "mask": {"name": "mask"}},
                    "viewer": {"tables": ["sales"], "columns": {"sales": ["region"]}, "mask": {}},
                }
            }
        },
    }


def _load_policy() -> dict:
    path = Path(settings.policy_path)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return _default_policy()


_ROLES = {"admin", "analyst", "viewer"}
_MASK_MODES = {"mask", "partial"}


def get_role_policy(datasource_id: str, role: str) -> dict:
    """Return a copy of one role policy for the administrator UI."""
    policy = _load_policy()
    ds = policy.get("datasources", {}).get(datasource_id) or policy.get("datasources", {}).get("default", {})
    value = (ds.get("roles", {}) or {}).get(role)
    if not value:
        value = {"tables": [], "columns": {}, "mask": {}}
    return {
        "tables": value.get("tables", "*"),
        "columns": value.get("columns", "*"),
        "mask": value.get("mask", {}),
    }


def update_role_policy(datasource_id: str, role: str, value: dict, available_schema: dict[str, set[str]]) -> dict:
    """Validate and atomically persist one datasource/role policy."""
    if role not in _ROLES:
        raise ValueError("角色无效")
    tables = value.get("tables", "*")
    columns = value.get("columns", "*")
    mask = value.get("mask", {})
    known = {str(table).lower(): {str(column).lower() for column in cols} for table, cols in available_schema.items()}
    if tables != "*":
        if not isinstance(tables, list) or not tables or any(str(table).lower() not in known for table in tables):
            raise ValueError("包含未知数据表")
        tables = list(dict.fromkeys(str(table) for table in tables))
    if columns != "*":
        if not isinstance(columns, dict):
            raise ValueError("字段权限格式无效")
        normalized = {}
        for table, cols in columns.items():
            table_name = str(table).lower()
            if table_name not in known or not isinstance(cols, list) or not cols:
                raise ValueError("包含未知数据表或字段")
            if any(str(column).lower() not in known[table_name] for column in cols):
                raise ValueError("包含未知数据表或字段")
            normalized[str(table)] = list(dict.fromkeys(str(column) for column in cols))
        columns = normalized
    if not isinstance(mask, dict) or any(str(mode) not in _MASK_MODES for mode in mask.values()):
        raise ValueError("脱敏模式只能是 mask 或 partial")
    if any(str(column).lower() not in {c for cols in known.values() for c in cols} for column in mask):
        raise ValueError("脱敏字段不存在")

    policy = _load_policy()
    datasources = policy.setdefault("datasources", {})
    if datasource_id not in datasources:
        default_roles = (datasources.get("default", {}).get("roles", {}) or {})
        datasources[datasource_id] = {"roles": json.loads(json.dumps(default_roles))}
    ds = datasources[datasource_id]
    ds.setdefault("roles", {})[role] = {"tables": tables, "columns": columns, "mask": mask}
    path = Path(settings.policy_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(policy, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return ds["roles"][role]


def resolve_policy(identity: dict) -> dict:
    """Return the effective access policy for an identity.

    Returns ``{"tables", "columns", "mask"}`` where ``tables``/``columns`` are
    either ``"*"`` or explicit collections.
    """
    roles = identity.get("roles") or []
    if "admin" in roles:
        return {"tables": "*", "columns": "*", "mask": {}}

    datasource = identity.get("datasource") or "default"
    ds_policies = _load_policy().get("datasources", {})
    role_policies = ds_policies.get(datasource, ds_policies.get("default", {})).get("roles", {})

    tables: set[str] = set()
    columns: dict[str, list[str]] = {}
    mask: dict[str, str] = {}
    full_tables = False
    full_columns = False
    merged = False

    for role in roles:
        entry = role_policies.get(role)
        if not entry:
            continue
        merged = True
        if entry.get("tables", "*") == "*":
            full_tables = True
        elif not full_tables:
            tables.update(entry["tables"])
        entry_columns = entry.get("columns", "*")
        if entry_columns == "*":
            full_columns = True
        elif not full_columns:
            for table, cols in entry_columns.items():
                current = columns.get(table)
                if current != "*":
                    columns[table] = list(dict.fromkeys((current or []) + list(cols)))
        mask.update(entry.get("mask", {}))

    if not merged:
        return {"tables": [], "columns": {}, "mask": {}}
    return {
        "tables": "*" if full_tables else sorted(tables),
        "columns": "*" if full_columns else columns,
        "mask": mask,
    }


def filter_schema(schema: dict[str, list[str]], identity: dict) -> dict[str, list[str]]:
    """Return only tables and columns visible to the effective role policy."""
    policy = resolve_policy(identity)
    if policy["tables"] == "*" and policy["columns"] == "*":
        return schema
    allowed_tables = set(schema) if policy["tables"] == "*" else {str(table).lower() for table in policy["tables"]}
    if policy["columns"] == "*":
        return {table: columns for table, columns in schema.items() if table.lower() in allowed_tables}
    allowed_columns = {table.lower(): {column.lower() for column in columns} for table, columns in policy["columns"].items()}
    return {table: [column for column in columns if column.lower() in allowed_columns.get(table.lower(), set())] for table, columns in schema.items() if table.lower() in allowed_tables}


def _has_projection_star(tree: exp.Select) -> bool:
    for expression in tree.expressions:
        if isinstance(expression, exp.Star):
            return True
        if isinstance(expression, exp.Column) and isinstance(expression.args.get("this"), exp.Star):
            return True
    return False


def apply_policy(sql: str, identity: dict, *, dialect: str = "sqlite") -> str:
    """Validate that ``sql`` only touches tables/columns the role may access.

    Raises :class:`QueryError` with ``E_TABLE_FORBIDDEN`` or
    ``E_COLUMN_FORBIDDEN`` on violations. Returns the SQL unchanged otherwise.
    """
    policy = resolve_policy(identity)
    if policy["tables"] == []:
        raise QueryError("E_TABLE_FORBIDDEN", "当前角色没有可查询的表")

    try:
        tree = sqlglot.parse_one(sql, read=validation_dialect(dialect))
    except Exception:
        return sql  # guardrails is responsible for syntax errors

    tables = policy["tables"]
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if tables != "*" and name not in tables:
            raise QueryError("E_TABLE_FORBIDDEN", f"当前角色无权查询表：{table.name}")

    columns = policy["columns"]
    if columns != "*":
        if _has_projection_star(tree):
            raise QueryError("E_COLUMN_FORBIDDEN", "当前角色无权使用 SELECT *，请显式指定允许的字段")
        table_names = {table.name.lower() for table in tree.find_all(exp.Table)}
        aliases = {table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)}
        allowed_by_table = {table.lower(): {column.lower() for column in cols} for table, cols in columns.items()}
        for column in tree.find_all(exp.Column):
            name = column.name.lower()
            qualifier = aliases.get((column.table or "").lower(), (column.table or "").lower())
            if qualifier:
                permitted = name in allowed_by_table.get(qualifier, set())
            elif len(table_names) == 1:
                table_name = next(iter(table_names))
                permitted = name in allowed_by_table.get(table_name, set())
            else:
                # An unqualified column in a join is safe only when every
                # referenced table explicitly permits that column.
                permitted = bool(table_names) and all(name in allowed_by_table.get(table, set()) for table in table_names)
            if not permitted:
                raise QueryError("E_COLUMN_FORBIDDEN", f"当前角色无权查询字段：{column.name}")

    return sql


def _apply_mask(value: str, mode: str) -> str:
    if mode == "mask":
        return "***"
    if mode == "partial":
        if len(value) <= 2:
            return "***"
        return value[0] + "***" + value[-1]
    return value


def redact_rows(columns: list[str], rows: list[list], identity: dict):
    """Mask sensitive column values in result rows according to the role policy."""
    mask = resolve_policy(identity).get("mask", {})
    if not mask:
        return rows

    indexes = []
    for index, column in enumerate(columns):
        mode = mask.get(column) or mask.get(column.lower())
        if mode:
            indexes.append((index, mode))

    if not indexes:
        return rows

    redacted = []
    for row in rows:
        new_row = list(row)
        for index, mode in indexes:
            if new_row[index] is not None:
                new_row[index] = _apply_mask(str(new_row[index]), mode)
        redacted.append(new_row)
    return redacted


_SAFE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_safe_scope_column(column: str) -> bool:
    """Return True when ``column`` is a plausible identifier (guards against injection)."""
    return bool(column) and bool(_SAFE_COLUMN.match(column))


def apply_row_filter(sql: str, scope_column: str, values: list[str]) -> tuple[str, dict]:
    """Inject a row-level filter ``scope_column IN (:grantN)`` into the query.

    Returns ``(rewritten_sql, extra_params)``. The named parameters must be merged
    into the execution params by the caller.
    """
    if not values or not is_safe_scope_column(scope_column):
        return sql, {}
    placeholders = ", ".join(f":grant{i}" for i in range(len(values)))
    predicate = f"{scope_column} IN ({placeholders})"
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
        condition = sqlglot.parse_one(predicate, read="sqlite")
        tree = tree.where(condition, append=True)
        new_sql = tree.sql(dialect="sqlite")
    except Exception:
        return sql, {}
    return new_sql, {f"grant{i}": value for i, value in enumerate(values)}
