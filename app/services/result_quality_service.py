"""Deterministic, privacy-preserving result quality checks.

The service only consumes authorized metadata, the validated SQL AST, an
EXPLAIN summary, and an in-memory result used to derive aggregate statistics.
Raw result values are never included in the returned evidence or critic input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import sqlglot
from sqlglot import exp

from app.agent.reflection import (
    ReflectionAction,
    ReflectionFailureType,
    ReflectionResult,
    sanitize_reflection_text,
)


_TOP_N = re.compile(r"(?:前\s*|top\s*)(\d{1,5})", re.IGNORECASE)
_EXPLICIT_DATE = re.compile(r"(?:19|20)\d{2}(?:[-/.年](?:0?[1-9]|1[0-2])(?:[-/.月](?:0?[1-9]|[12]\d|3[01]))?)?")
_ACTIVE_TERMS = ("当前", "有效", "在用", "未删除", "active", "current")
_STATUS_COLUMNS = {"status", "state", "order_status", "record_status"}
_SOFT_DELETE_COLUMNS = {"deleted_at", "is_deleted", "delete_flag", "deleted"}
_TIME_COLUMNS = {"date", "created_at", "updated_at", "order_date", "event_time", "snapshot_date"}


@dataclass(frozen=True)
class QualityFinding:
    code: str
    failure_type: ReflectionFailureType
    severity: str
    evidence: str


@dataclass(frozen=True)
class ResultQualityAssessment:
    outcome: str
    reflection: ReflectionResult
    reason_codes: list[str]
    warning_codes: list[str]
    safe_summary: dict[str, Any]
    critic_context: dict[str, Any]
    findings: list[QualityFinding] = field(default_factory=list)


class ResultQualityService:
    """Apply conservative rules before any model-based semantic critic."""

    def assess(
        self,
        *,
        question: str,
        sql: str,
        columns: list[str] | tuple[str, ...],
        rows: list[list[Any]] | list[tuple[Any, ...]],
        truncated: bool,
        authorized_schema: dict[str, set[str] | list[str] | tuple[str, ...]],
        relationships: list[dict[str, Any]],
        schema_details: dict[str, Any],
        expected_dimensions: Iterable[str] = (),
        expected_metrics: Iterable[str] = (),
        explain_summary: dict[str, Any] | None = None,
    ) -> ResultQualityAssessment:
        normalized_columns = [str(column).strip()[:256] for column in columns]
        dimensions = _normalized_names(expected_dimensions)
        metrics = _normalized_names(expected_metrics)
        safe_summary = _safe_result_summary(normalized_columns, rows, truncated, dimensions)
        findings: list[QualityFinding] = []

        if any(not isinstance(row, (list, tuple)) or len(row) != len(normalized_columns) for row in rows):
            findings.append(_finding("RESULT_SHAPE_MISMATCH", ReflectionFailureType.RESULT_SHAPE, "blocking", "column_count does not match one or more result rows"))

        try:
            statement = sqlglot.parse_one(sql)
        except sqlglot.errors.ParseError:
            statement = None
            findings.append(_finding("RESULT_SQL_AST_UNAVAILABLE", ReflectionFailureType.RESULT_SHAPE, "blocking", "validated SQL could not be parsed for result verification"))

        projected = {name.lower() for name in normalized_columns}
        if statement is not None:
            projected.update(_projection_names(statement))
        for name in sorted(dimensions - projected):
            findings.append(_finding("EXPECTED_DIMENSION_MISSING", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", f"missing_dimension={name}"))
        for name in sorted(metrics - projected):
            findings.append(_finding("EXPECTED_METRIC_MISSING", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", f"missing_metric={name}"))

        top_match = _TOP_N.search(question)
        if top_match and statement is not None:
            if statement.args.get("order") is None:
                findings.append(_finding("TOP_N_ORDER_MISSING", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", "top_n request has no ORDER BY"))
            limit = statement.args.get("limit")
            limit_value = _limit_value(limit)
            requested = int(top_match.group(1))
            if limit_value is None or limit_value > requested:
                findings.append(_finding("TOP_N_LIMIT_MISSING", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", f"requested_top_n={requested};sql_limit={limit_value or 'none'}"))

        if statement is not None:
            findings.extend(_relationship_findings(statement, relationships))
            findings.extend(_scope_findings(question, statement, authorized_schema, schema_details))
            if _EXPLICIT_DATE.search(question) and not _has_time_filter(statement):
                findings.append(_finding("TIME_RANGE_SCOPE_UNCERTAIN", ReflectionFailureType.BUSINESS_AMBIGUITY, "uncertain", "question contains an explicit date but SQL time filtering is not provable"))

        duplicate_count = safe_summary.get("duplicate_dimension_count", 0)
        grouped_or_aggregated = bool(
            statement is not None
            and (statement.find(exp.Group) is not None or any(statement.find_all(exp.AggFunc)))
        )
        if dimensions and duplicate_count and grouped_or_aggregated:
            findings.append(_finding("DUPLICATE_DIMENSION_GRAIN", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", f"duplicate_dimension_groups={duplicate_count}"))

        if not rows:
            findings.append(_finding("EMPTY_RESULT", ReflectionFailureType.EMPTY_RESULT, "blocking", "row_count=0"))
        if truncated:
            findings.append(_finding("RESULT_TRUNCATED", ReflectionFailureType.RESULT_SHAPE, "warning", "result exceeded the configured row window"))

        blocking = _unique_findings(item for item in findings if item.severity == "blocking")
        uncertain = _unique_findings(item for item in findings if item.severity == "uncertain")
        warnings = _unique_findings(item for item in findings if item.severity == "warning")
        if blocking:
            failure_type = _dominant_failure_type(blocking)
            action = ReflectionAction.FAIL if failure_type == ReflectionFailureType.RESULT_SHAPE else ReflectionAction.REGENERATE_SQL
            reflection = ReflectionResult(
                accepted=False,
                failure_type=failure_type,
                reason_codes=[item.code for item in blocking][:8],
                evidence=[item.evidence for item in blocking][:8],
                action=action,
                repair_instruction=_repair_instruction(failure_type),
                retryable=action == ReflectionAction.REGENERATE_SQL,
                confidence=1.0,
            )
            outcome = "rejected"
            reason_codes = list(reflection.reason_codes)
        elif uncertain:
            reflection = ReflectionResult(
                accepted=False,
                failure_type=ReflectionFailureType.BUSINESS_AMBIGUITY,
                reason_codes=[item.code for item in uncertain][:8],
                evidence=[item.evidence for item in uncertain][:8],
                action=ReflectionAction.ASK_CLARIFICATION,
                repair_instruction="请确认业务状态、时间或快照口径后再执行查询。",
                retryable=False,
                confidence=1.0,
            )
            outcome = "uncertain"
            reason_codes = list(reflection.reason_codes)
        else:
            warning_codes = [item.code for item in warnings][:8]
            reflection = ReflectionResult(
                accepted=True,
                failure_type=ReflectionFailureType.NONE,
                reason_codes=warning_codes or ["RESULT_QUALITY_ACCEPTED"],
                evidence=[item.evidence for item in warnings][:8],
                action=ReflectionAction.ACCEPT,
                retryable=False,
                confidence=1.0,
            )
            outcome = "accepted"
            reason_codes = []

        safe_sql = _redacted_sql(statement, sql)
        referenced_tables = _table_context(statement)[0] if statement is not None else set()
        schema_items = [
            (str(table), values)
            for table, values in sorted(authorized_schema.items())
            if str(table).lower() in referenced_tables
        ][:20]
        critic_context = {
            "question": sanitize_reflection_text(question),
            "sql": safe_sql,
            "authorized_schema": {
                str(table)[:128]: sorted(str(column)[:128] for column in values)
                for table, values in schema_items
            },
            "relationships": [_safe_relationship(item) for item in relationships[:100]],
            "reason_codes": reason_codes,
            "evidence": [item.evidence for item in (*blocking, *uncertain)][:8],
            "result_summary": safe_summary,
            "explain_summary": _safe_explain_summary(explain_summary or {}),
        }
        return ResultQualityAssessment(
            outcome=outcome,
            reflection=reflection,
            reason_codes=reason_codes,
            warning_codes=[item.code for item in warnings][:8],
            safe_summary=safe_summary,
            critic_context=critic_context,
            findings=findings,
        )


def _normalized_names(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _finding(code: str, failure_type: ReflectionFailureType, severity: str, evidence: str) -> QualityFinding:
    return QualityFinding(code=code, failure_type=failure_type, severity=severity, evidence=sanitize_reflection_text(evidence))


def _unique_findings(values: Iterable[QualityFinding]) -> list[QualityFinding]:
    result: list[QualityFinding] = []
    seen: set[str] = set()
    for value in values:
        if value.code in seen:
            continue
        seen.add(value.code)
        result.append(value)
    return result


def _projection_names(statement: exp.Expression) -> set[str]:
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        return set()
    names: set[str] = set()
    for projection in select.expressions:
        name = str(projection.alias_or_name or "").strip().lower()
        if name:
            names.add(name)
    return names


def _limit_value(limit: exp.Expression | None) -> int | None:
    if limit is None:
        return None
    expression = limit.args.get("expression")
    if isinstance(expression, exp.Literal) and expression.is_int:
        return int(expression.this)
    return None


def _table_context(statement: exp.Expression) -> tuple[set[str], dict[str, str], set[str]]:
    cte_names = {str(cte.alias_or_name).lower() for cte in statement.find_all(exp.CTE)}
    aliases: dict[str, str] = {}
    tables: set[str] = set()
    for table in statement.find_all(exp.Table):
        name = str(table.name).lower()
        alias = str(table.alias_or_name).lower()
        aliases[alias] = name
        aliases[name] = name
        if name not in cte_names:
            tables.add(name)
    preaggregated: set[str] = set()
    for cte in statement.find_all(exp.CTE):
        query = cte.this
        if query.find(exp.Group) is not None or any(query.find_all(exp.AggFunc)):
            preaggregated.update(str(table.name).lower() for table in query.find_all(exp.Table))
    return tables, aliases, preaggregated


def _relationship_findings(statement: exp.Expression, relationships: list[dict[str, Any]]) -> list[QualityFinding]:
    if not any(statement.find_all(exp.AggFunc)) or not any(statement.find_all(exp.Join)):
        return []
    tables, aliases, preaggregated = _table_context(statement)
    aggregate_tables: set[str] = set()
    for aggregate in statement.find_all(exp.AggFunc):
        for column in aggregate.find_all(exp.Column):
            qualifier = str(column.table or "").lower()
            if qualifier:
                aggregate_tables.add(aliases.get(qualifier, qualifier))
    findings: list[QualityFinding] = []
    normalized: list[tuple[str, str, str]] = []
    for relationship in relationships:
        from_table = str(relationship.get("from_table", "")).lower()
        to_table = str(relationship.get("to_table", "")).lower()
        cardinality = str(relationship.get("cardinality", "many_to_one")).lower()
        if from_table and to_table:
            normalized.append((from_table, to_table, cardinality))
        if from_table not in tables or to_table not in tables or from_table in preaggregated:
            continue
        one_side = to_table if cardinality == "many_to_one" else from_table if cardinality == "one_to_many" else ""
        many_side = from_table if cardinality == "many_to_one" else to_table if cardinality == "one_to_many" else ""
        if cardinality == "many_to_many" or one_side in aggregate_tables:
            findings.append(_finding("AGGREGATE_FANOUT_RISK", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", f"aggregate_from={one_side or 'unknown'};many_side={many_side or 'unknown'}"))

    by_dimension: dict[str, set[str]] = {}
    for from_table, to_table, cardinality in normalized:
        if cardinality == "many_to_one" and from_table in tables and to_table in tables:
            by_dimension.setdefault(to_table, set()).add(from_table)
    for dimension, facts in by_dimension.items():
        aggregated_facts = facts & aggregate_tables
        if len(aggregated_facts) >= 2 and not aggregated_facts.issubset(preaggregated):
            findings.append(_finding("DOUBLE_FACT_PREAGGREGATION_REQUIRED", ReflectionFailureType.AGGREGATION_GRAIN, "blocking", f"dimension={dimension};fact_count={len(aggregated_facts)}"))
    return findings


def _scope_findings(question: str, statement: exp.Expression, authorized_schema: dict[str, Any], schema_details: dict[str, Any]) -> list[QualityFinding]:
    lowered_question = question.lower()
    if not any(term in lowered_question for term in _ACTIVE_TERMS):
        return []
    available_columns = {
        str(column).lower()
        for columns in authorized_schema.values()
        for column in columns
    }
    available_columns.update(_schema_detail_columns(schema_details))
    filtered_columns = {
        str(column.name).lower()
        for where in statement.find_all(exp.Where)
        for column in where.find_all(exp.Column)
    }
    findings: list[QualityFinding] = []
    if available_columns & _STATUS_COLUMNS and not filtered_columns & _STATUS_COLUMNS:
        findings.append(_finding("STATUS_SCOPE_UNCERTAIN", ReflectionFailureType.BUSINESS_AMBIGUITY, "uncertain", "active/current wording is present but status filtering is not provable"))
    if available_columns & _SOFT_DELETE_COLUMNS and not filtered_columns & _SOFT_DELETE_COLUMNS:
        findings.append(_finding("SOFT_DELETE_SCOPE_UNCERTAIN", ReflectionFailureType.BUSINESS_AMBIGUITY, "uncertain", "active/current wording is present but soft-delete filtering is not provable"))
    return findings


def _schema_detail_columns(schema_details: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    tables = schema_details.get("tables", {}) if isinstance(schema_details, dict) else {}
    if not isinstance(tables, dict):
        return result
    for table in tables.values():
        if not isinstance(table, dict):
            continue
        columns = table.get("columns", [])
        if isinstance(columns, dict):
            result.update(str(name).lower() for name in columns)
        elif isinstance(columns, (list, tuple, set)):
            for column in columns:
                result.add(str(column.get("name") if isinstance(column, dict) else column).lower())
    return result


def _has_time_filter(statement: exp.Expression) -> bool:
    for where in statement.find_all(exp.Where):
        for column in where.find_all(exp.Column):
            name = str(column.name).lower()
            if name in _TIME_COLUMNS or any(token in name for token in ("date", "time", "month", "year", "day")):
                return True
    return False


def _safe_result_summary(columns: list[str], rows: list[Any], truncated: bool, dimensions: set[str]) -> dict[str, Any]:
    null_counts = {column: 0 for column in columns}
    type_names = {column: set() for column in columns}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            continue
        for index, value in enumerate(row):
            if value is None:
                null_counts[columns[index]] += 1
            else:
                type_names[columns[index]].add(type(value).__name__[:32])
    duplicate_count = 0
    indexes = [index for index, column in enumerate(columns) if column.lower() in dimensions]
    if indexes:
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != len(columns):
                continue
            fingerprint = tuple(f"{type(row[index]).__name__}:{repr(row[index])}" for index in indexes)
            if fingerprint in seen:
                duplicate_count += 1
            seen.add(fingerprint)
    return {
        "columns": columns,
        "row_count": len(rows),
        "truncated": bool(truncated),
        "null_counts": null_counts,
        "type_summary": {column: sorted(values) for column, values in type_names.items()},
        "duplicate_dimension_count": duplicate_count,
    }


def _redacted_sql(statement: exp.Expression | None, original: str) -> str:
    if statement is None:
        return ""

    def redact(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Literal) and not isinstance(node.parent, (exp.Limit, exp.Offset)):
            return exp.Literal.string("[REDACTED]") if node.is_string else exp.Literal.number("0")
        return node

    try:
        return statement.copy().transform(redact).sql()[:5000]
    except Exception:
        return sanitize_reflection_text(original)[:5000]


def _safe_relationship(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "from_table": str(value.get("from_table", ""))[:128],
        "from_columns": [str(item)[:128] for item in value.get("from_columns", [])[:8]],
        "to_table": str(value.get("to_table", ""))[:128],
        "to_columns": [str(item)[:128] for item in value.get("to_columns", [])[:8]],
        "cardinality": str(value.get("cardinality", "unknown"))[:32],
        "confidence": max(0.0, min(float(value.get("confidence", 0) or 0), 1.0)),
    }


def _safe_explain_summary(value: dict[str, Any]) -> dict[str, Any]:
    allowed = ("backend", "supported", "estimated_rows", "estimated_cost", "full_scan", "index_used", "reason")
    return {key: value.get(key) for key in allowed if key in value}


def _dominant_failure_type(findings: list[QualityFinding]) -> ReflectionFailureType:
    order = (
        ReflectionFailureType.RESULT_SHAPE,
        ReflectionFailureType.AGGREGATION_GRAIN,
        ReflectionFailureType.RELATIONSHIP,
        ReflectionFailureType.EMPTY_RESULT,
        ReflectionFailureType.BUSINESS_AMBIGUITY,
    )
    types = {item.failure_type for item in findings}
    return next((value for value in order if value in types), ReflectionFailureType.UNKNOWN)


def _repair_instruction(failure_type: ReflectionFailureType) -> str:
    return {
        ReflectionFailureType.AGGREGATION_GRAIN: "修正查询粒度；一对多或双事实表必须先在各自粒度预聚合再关联。",
        ReflectionFailureType.RELATIONSHIP: "仅使用已授权且有关系证据的关联路径重新生成 SQL。",
        ReflectionFailureType.EMPTY_RESULT: "检查筛选条件和授权 Schema 后重新生成 SQL，不得放宽权限。",
        ReflectionFailureType.RESULT_SHAPE: "结果结构异常，停止自动执行并保留安全错误码。",
    }.get(failure_type, "根据结构化证据修正 SQL，不得放宽任何安全门禁。")

