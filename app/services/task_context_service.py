"""Build permission-aware, compact context for task-level follow-up queries."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError


CONVERSATION_CONTEXT_VERSION = "conversation-context-v2"
_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,}")


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _allowed_schema(value: Mapping[str, Any] | None) -> dict[str, set[str]] | None:
    if value is None:
        return None
    return {
        str(table).lower(): {str(column).lower() for column in (columns or [])}
        for table, columns in value.items()
    }


def _sql_lineage(sql: str, allowed_schema: dict[str, set[str]] | None) -> dict[str, Any] | None:
    try:
        statement = parse_one(str(sql or ""))
    except (SqlglotError, ValueError, TypeError):
        return None
    cte_names = {
        str(cte.alias_or_name).lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    tables = sorted({
        str(table.name).lower()
        for table in statement.find_all(exp.Table)
        if table.name and str(table.name).lower() not in cte_names
    })
    columns = sorted({
        str(column.name).lower()
        for column in statement.find_all(exp.Column)
        if column.name and column.name != "*"
    })
    if allowed_schema is not None:
        if any(table not in allowed_schema for table in tables):
            return None
        allowed_columns = {
            column
            for table in tables
            for column in allowed_schema.get(table, set())
        }
        if any(column not in allowed_columns for column in columns):
            return None
    return {
        "tables": tables,
        "columns": columns,
        "has_group_by": statement.args.get("group") is not None,
        "has_order_by": statement.args.get("order") is not None,
        "has_limit": statement.args.get("limit") is not None,
        "distinct": bool(statement.args.get("distinct")),
    }


def _extract_turns(
    detail: Mapping[str, Any],
    *,
    allowed_schema: dict[str, set[str]] | None,
) -> tuple[list[dict[str, Any]], int]:
    steps = sorted(
        detail.get("steps", []) or [],
        key=lambda item: int(_value(item, "sequence", 0) or 0),
    )
    turns: list[dict[str, Any]] = []
    source_count = 0
    pending: dict[str, Any] | None = None
    for step in steps:
        kind = str(_value(step, "kind", ""))
        input_data = _value(step, "input", {}) or {}
        output = _value(step, "output", {}) or {}
        status = str(_value(step, "status", ""))
        sequence = int(_value(step, "sequence", 0) or 0)
        if kind == "understand_question":
            pending = {
                "question": str(input_data.get("question", ""))[:500],
                "trace_id": str(output.get("trace_id", ""))[:128],
                "understand_sequence": sequence,
            }
            continue
        if kind != "query" or status != "completed" or pending is None or not output.get("sql"):
            continue
        source_count += 1
        lineage = _sql_lineage(str(output.get("sql", "")), allowed_schema)
        if lineage is not None:
            query_sequence = sequence
            turns.append({
                "ref": f"steps:{pending['understand_sequence']}-{query_sequence}",
                "source_steps": [pending["understand_sequence"], query_sequence],
                "question": pending["question"],
                "trace_id": pending["trace_id"],
                "lineage": {
                    **lineage,
                    "result_columns": [
                        str(column)[:128]
                        for column in (output.get("columns") or [])[:100]
                    ],
                    "row_count": max(0, int(output.get("row_count", 0) or 0)),
                },
            })
        pending = None
    return turns, source_count


def _terms(value: str) -> set[str]:
    return {match.group(0).lower() for match in _TERM.finditer(str(value))}


def _relevance(turn: dict[str, Any], query_terms: set[str]) -> int:
    lineage = turn["lineage"]
    candidates = set(lineage.get("tables", [])) | set(lineage.get("columns", []))
    candidates |= _terms(turn.get("question", ""))
    return len(query_terms & candidates)


def _fact(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": turn["ref"],
        "source_steps": list(turn["source_steps"]),
        "lineage": dict(turn["lineage"]),
    }


def _serialized_chars(context: dict[str, Any]) -> int:
    return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))


def _refresh_budget_stats(context: dict[str, Any], *, dropped_facts: int) -> int:
    compaction = context["compaction"]
    compaction["dropped_fact_count"] = dropped_facts
    compaction["recent_turn_count"] = len(context["turns"])
    compaction["retrieved_turn_count"] = len(context["retrieved_turns"])
    compaction["serialized_chars"] = 0
    while True:
        size = _serialized_chars(context)
        if size == compaction["serialized_chars"]:
            return size
        compaction["serialized_chars"] = size


def _fit_budget(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    dropped_facts = 0
    while True:
        size = _refresh_budget_stats(context, dropped_facts=dropped_facts)
        if size <= max_chars:
            return context
        facts = context["summary"]["facts"]
        if facts:
            facts.pop(0)
            dropped_facts += 1
            continue
        if context["retrieved_turns"]:
            context["retrieved_turns"].pop()
            continue
        if len(context["turns"]) > 1:
            context["turns"].pop(0)
            continue
        shortened = False
        for collection in (context["turns"], context["retrieved_turns"]):
            for turn in collection:
                question = str(turn.get("question", ""))
                if len(question) > 120:
                    turn["question"] = question[:120]
                    shortened = True
        if shortened:
            continue
        if context["turns"]:
            context["turns"].pop(0)
            continue
        branch_source = context.get("branch_source")
        if isinstance(branch_source, dict):
            lineage = branch_source.get("lineage")
            if isinstance(lineage, dict) and lineage:
                for field in ("columns", "tables"):
                    values = lineage.get(field)
                    if isinstance(values, list) and values:
                        values.pop()
                        branch_source["lineage_truncated"] = True
                        break
                else:
                    lineage.clear()
                    branch_source["lineage_truncated"] = True
                continue
        return context


def build_follow_up_context(
    detail: Mapping[str, Any],
    *,
    question: str = "",
    allowed_schema: Mapping[str, Any] | None = None,
    max_turns: int | None = None,
    compaction_threshold: int = 8,
    recent_turns: int = 4,
    retrieval_turns: int = 2,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Return deterministic lineage facts while keeping original steps authoritative.

    Result rows, parameter values, raw SQL and generated explanations are never
    copied into model context. Historical turns are revalidated against the
    caller's current visible schema before they can influence a follow-up.
    """
    task = detail.get("task")
    task_id = str(_value(task, "id", "")) if task is not None else ""
    datasource_id = str(_value(task, "datasource_id", "")) if task is not None else ""
    visible, source_count = _extract_turns(
        detail,
        allowed_schema=_allowed_schema(allowed_schema),
    )
    threshold = max(1, int(compaction_threshold))
    recent_limit = max(1, int(max_turns if max_turns is not None else recent_turns))
    retrieval_limit = max(0, int(retrieval_turns))
    budget = max(1000, int(max_chars))

    if len(visible) > threshold:
        recent = visible[-recent_limit:]
        older = visible[:-recent_limit]
    else:
        recent = visible[-recent_limit:] if max_turns is not None else visible
        older = visible[: max(0, len(visible) - len(recent))]

    query_terms = _terms(question)
    ranked = sorted(
        (
            (_relevance(turn, query_terms), index, turn)
            for index, turn in enumerate(older)
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    retrieved = [turn for score, _index, turn in ranked if score > 0][:retrieval_limit]
    context = {
        "version": CONVERSATION_CONTEXT_VERSION,
        "task_id": task_id,
        "datasource_id": datasource_id,
        "compaction": {
            "source_turn_count": source_count,
            "visible_turn_count": len(visible),
            "filtered_turn_count": max(0, source_count - len(visible)),
            "compacted_turn_count": len(older),
            "recent_turn_count": len(recent),
            "retrieved_turn_count": len(retrieved),
            "budget_chars": budget,
            "serialized_chars": 0,
            "dropped_fact_count": 0,
        },
        "summary": {
            "mode": "deterministic_lineage",
            "facts": [_fact(turn) for turn in older],
        },
        "turns": recent,
        "retrieved_turns": retrieved,
    }
    return _fit_budget(context, budget)


def build_branch_context(
    detail: Mapping[str, Any],
    *,
    source_trace_id: str,
    question: str = "",
    allowed_schema: Mapping[str, Any] | None = None,
    compaction_threshold: int = 8,
    recent_turns: int = 4,
    retrieval_turns: int = 2,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Build a bounded context ending at one completed historical query turn."""
    trace_id = str(source_trace_id or "").strip()
    steps = sorted(
        detail.get("steps", []) or [],
        key=lambda item: int(_value(item, "sequence", 0) or 0),
    )
    selected_query = None
    selected_understand = None
    pending_understand = None
    for step in steps:
        kind = str(_value(step, "kind", ""))
        if kind == "understand_question":
            pending_understand = step
            continue
        if kind != "query":
            continue
        output = _value(step, "output", {}) or {}
        if (
            str(_value(step, "status", "")) == "completed"
            and str(output.get("trace_id", "")) == trace_id
        ):
            selected_query = step
            selected_understand = pending_understand
            break
    if selected_query is None or selected_understand is None:
        raise ValueError("E_BRANCH_SOURCE_TURN:只能从已完成的历史查询轮次创建分支")

    output = _value(selected_query, "output", {}) or {}
    lineage = _sql_lineage(
        str(output.get("sql", "")),
        _allowed_schema(allowed_schema),
    )
    if lineage is None:
        raise PermissionError("E_BRANCH_SOURCE_FORBIDDEN:来源轮次不在当前可见 Schema 范围内")

    selected_sequence = int(_value(selected_query, "sequence", 0) or 0)
    truncated_detail = {
        **dict(detail),
        "steps": [
            step
            for step in steps
            if int(_value(step, "sequence", 0) or 0) <= selected_sequence
        ],
    }
    context = build_follow_up_context(
        truncated_detail,
        question=question,
        allowed_schema=allowed_schema,
        compaction_threshold=compaction_threshold,
        recent_turns=recent_turns,
        retrieval_turns=retrieval_turns,
        max_chars=max_chars,
    )
    understand_sequence = int(_value(selected_understand, "sequence", 0) or 0)
    context["branch_source"] = {
        "task_id": str(_value(detail.get("task"), "id", "")),
        "trace_id": trace_id,
        "ref": f"steps:{understand_sequence}-{selected_sequence}",
        "source_steps": [understand_sequence, selected_sequence],
        "lineage": lineage,
    }
    return _fit_budget(context, max(1000, int(max_chars)))


__all__ = [
    "CONVERSATION_CONTEXT_VERSION",
    "build_branch_context",
    "build_follow_up_context",
]
