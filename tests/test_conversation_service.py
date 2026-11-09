from types import SimpleNamespace

from app.services.conversation_service import build_task_conversation


def item(**values):
    return SimpleNamespace(**values)


def test_projects_multiple_task_turns_with_matching_result_artifacts():
    detail = {
        "task": item(id="task-1", title="", question="查询销售额", datasource_id="sqlite-demo", status="completed"),
        "steps": [
            item(sequence=0, kind="understand_question", status="completed", input={"question": "查询销售额", "follow_up": False}, output={"trace_id": "trace-1"}),
            item(sequence=1, kind="query", status="completed", input={}, output={"trace_id": "trace-1", "sql": "SELECT amount FROM sales", "columns": ["amount"], "row_count": 1}),
            item(sequence=2, kind="explain_result", status="completed", input={}, output={"summary": "销售额为 100"}),
            item(sequence=3, kind="understand_question", status="completed", input={"question": "按区域拆分", "follow_up": True}, output={"trace_id": "trace-2"}),
            item(sequence=4, kind="query", status="completed", input={}, output={"trace_id": "trace-2", "sql": "SELECT region, amount FROM sales", "columns": ["region", "amount"], "row_count": 2}),
            item(sequence=5, kind="verify_result", status="completed", input={}, output={"truncated": True}),
            item(sequence=6, kind="explain_result", status="completed", input={}, output={"summary": "华东最高"}),
        ],
        "artifacts": [
            item(kind="table", metadata={"columns": ["amount"], "rows": [[100]], "row_count": 1}),
            item(kind="chart", metadata={}),
            item(kind="table", metadata={"columns": ["region", "amount"], "rows": [["华东", 60], ["华南", 40]], "row_count": 2, "truncated": True}),
        ],
    }

    conversation = build_task_conversation(detail)

    assert conversation["task_id"] == "task-1"
    assert conversation["title"] == "查询销售额"
    assert [turn["question"] for turn in conversation["turns"]] == ["查询销售额", "按区域拆分"]
    assert conversation["turns"][1]["follow_up"] is True
    assert conversation["turns"][0]["result"]["rows"] == [[100]]
    assert conversation["turns"][1]["result"]["rows"][0] == ["华东", 60]
    assert conversation["turns"][1]["result"]["truncated"] is True


def test_projects_pending_approval_and_failed_turn_without_result_rows():
    detail = {
        "task": item(id="task-2", title="风险查询", question="查询全部", datasource_id="sqlite-demo", status="approval_required"),
        "steps": [
            item(sequence=0, kind="understand_question", status="completed", input={"question": "查询全部", "follow_up": False}, output={"trace_id": "trace-a"}),
            item(sequence=1, kind="approval", status="pending", input={}, output={"approval_id": "approval-1", "sql": "SELECT * FROM sales"}),
            item(sequence=2, kind="understand_question", status="completed", input={"question": "失败追问", "follow_up": True}, output={"trace_id": "trace-b"}),
            item(sequence=3, kind="query", status="failed", input={}, output={"sql": "SELECT bad FROM sales"}),
        ],
        "artifacts": [],
    }

    turns = build_task_conversation(detail)["turns"]

    assert turns[0]["status"] == "approval_required"
    assert turns[0]["approval_id"] == "approval-1"
    assert turns[1]["status"] == "failed"
    assert turns[1]["result"] is None
    assert turns[1]["error"] == "查询执行失败"


def test_projects_branch_origin_without_copying_parent_results():
    detail = {
        "task": item(
            id="branch-1",
            title="华东假设",
            question="只看华东",
            datasource_id="sqlite-demo",
            status="completed",
            parent_task_id="task-main",
            root_task_id="task-main",
            branch_source_trace_id="trace-main",
            branch_label="华东假设",
            branch_depth=1,
        ),
        "steps": [],
        "artifacts": [],
    }

    conversation = build_task_conversation(detail)

    assert conversation["branch_origin"] == {
        "parent_task_id": "task-main",
        "root_task_id": "task-main",
        "source_trace_id": "trace-main",
        "label": "华东假设",
        "depth": 1,
    }
    assert conversation["turns"] == []
