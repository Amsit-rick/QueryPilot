"""Project durable task steps and artifacts into user-facing conversation turns."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _value(item: object, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def build_task_conversation(detail: Mapping[str, Any]) -> dict[str, Any]:
    task = detail["task"]
    steps = sorted(detail.get("steps", []) or [], key=lambda item: _value(item, "sequence", 0))
    table_artifacts = [
        item
        for item in (detail.get("artifacts", []) or [])
        if _value(item, "kind") == "table" and isinstance(_value(item, "metadata", {}), Mapping)
    ]
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for step in steps:
        kind = _value(step, "kind", "")
        status = _value(step, "status", "")
        input_data = _value(step, "input", {}) or {}
        output = _value(step, "output", {}) or {}
        if kind == "understand_question":
            current = {
                "id": output.get("trace_id") or f"turn-{_value(step, 'sequence', len(turns))}",
                "question": str(input_data.get("question", "")),
                "follow_up": bool(input_data.get("follow_up")),
                "status": "running",
                "trace_id": str(output.get("trace_id", "")),
                "sql": "",
                "answer": "",
                "columns": [],
                "row_count": 0,
                "truncated": False,
                "analysis": None,
                "result": None,
            }
            turns.append(current)
            continue
        if current is None:
            continue
        if kind == "query":
            current["sql"] = str(output.get("sql", ""))
            current["columns"] = list(output.get("columns", []) or [])
            current["row_count"] = int(output.get("row_count", 0) or 0)
            current["trace_id"] = str(output.get("trace_id") or current["trace_id"])
            if status == "failed":
                current["status"] = "failed"
                current["error"] = "查询执行失败"
            elif status == "completed":
                current["status"] = "completed"
        elif kind == "verify_result" and status == "completed":
            current["truncated"] = bool(output.get("truncated", False))
        elif kind == "analyze_result" and status == "completed":
            current["analysis"] = dict(output)
        elif kind == "explain_result" and status == "completed":
            current["answer"] = str(output.get("summary", ""))
        elif kind == "approval" and status == "pending":
            current.update({
                "status": "approval_required",
                "sql": str(output.get("sql", "")),
                "approval_id": str(output.get("approval_id", "")),
                "message": "查询需要人工确认",
            })
        elif status == "failed":
            current["status"] = "failed"
            current["error"] = "查询处理失败"

    artifact_index = 0
    for turn in turns:
        if turn["status"] != "completed" or artifact_index >= len(table_artifacts):
            continue
        metadata = dict(_value(table_artifacts[artifact_index], "metadata", {}) or {})
        artifact_index += 1
        columns = list(metadata.get("columns", []) or turn["columns"])
        rows = list(metadata.get("rows", []) or [])
        row_count = int(metadata.get("row_count", turn["row_count"]) or 0)
        truncated = bool(metadata.get("truncated", turn["truncated"]))
        answer = turn["answer"] or f"查询完成，共 {row_count} 行"
        turn["result"] = {
            "task_id": _value(task, "id", ""),
            "trace_id": turn["trace_id"],
            "question": turn["question"],
            "follow_up": turn["follow_up"],
            "sql": turn["sql"],
            "columns": columns,
            "rows": rows,
            "row_count": row_count,
            "truncated": truncated,
            "answer": answer,
            "analysis": turn["analysis"],
        }

    branch_origin = None
    if _value(task, "parent_task_id", ""):
        branch_origin = {
            "parent_task_id": _value(task, "parent_task_id", ""),
            "root_task_id": _value(task, "root_task_id", ""),
            "source_trace_id": _value(task, "branch_source_trace_id", ""),
            "label": _value(task, "branch_label", ""),
            "depth": int(_value(task, "branch_depth", 0) or 0),
        }

    return {
        "task_id": _value(task, "id", ""),
        "title": _value(task, "title", "") or _value(task, "question", ""),
        "datasource_id": _value(task, "datasource_id", ""),
        "status": _value(task, "status", ""),
        "branch_origin": branch_origin,
        "turns": turns,
    }
