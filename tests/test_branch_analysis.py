from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import require_identity
from app.config import settings
from app.main import app
from app.schemas.queries import QueryRequest


def _query(client: TestClient, question: str, sql: str) -> dict:
    response = client.post(
        "/api/v1/query",
        json={"question": question, "sql": sql},
    )
    assert response.status_code == 200
    assert "error" not in response.json()
    return response.json()


def test_branch_request_requires_a_complete_origin_and_rejects_follow_up_mix():
    with pytest.raises(ValidationError):
        QueryRequest(question="分支", branch_from_task_id="task-1")

    with pytest.raises(ValidationError):
        QueryRequest(
            question="分支",
            task_id="task-current",
            follow_up=True,
            branch_from_task_id="task-1",
            branch_from_trace_id="trace-1",
        )


def test_branch_query_creates_an_isolated_task_and_safe_family_projection():
    client = TestClient(app)
    main = _query(client, "查询区域销售额", "SELECT region, SUM(amount) AS total FROM sales GROUP BY region")
    main_before = client.get(f"/api/v1/tasks/{main['task_id']}").json()

    branch_response = client.post(
        "/api/v1/query",
        json={
            "question": "假设只看华东区域",
            "sql": "SELECT region, SUM(amount) AS total FROM sales WHERE region = :region GROUP BY region",
            "params": {"region": "华东"},
            "branch_from_task_id": main["task_id"],
            "branch_from_trace_id": main["trace_id"],
            "branch_label": "华东假设",
        },
    )

    assert branch_response.status_code == 200
    branch = branch_response.json()
    assert branch["task_id"] != main["task_id"]
    assert branch["branch_origin"] == {
        "parent_task_id": main["task_id"],
        "root_task_id": main["task_id"],
        "source_trace_id": main["trace_id"],
        "label": "华东假设",
        "depth": 1,
    }

    main_after = client.get(f"/api/v1/tasks/{main['task_id']}").json()
    assert main_after["status"] == "completed"
    assert main_after["steps"] == main_before["steps"]
    assert main_after["artifacts"] == main_before["artifacts"]
    assert [event["kind"] for event in main_after["events"]][-1] == "task_branch_spawned"

    branch_detail = client.get(f"/api/v1/tasks/{branch['task_id']}").json()
    assert branch_detail["parent_task_id"] == main["task_id"]
    assert branch_detail["root_task_id"] == main["task_id"]
    assert branch_detail["branch_source_trace_id"] == main["trace_id"]
    assert branch_detail["branch_depth"] == 1
    assert any(event["kind"] == "task_branch_created" for event in branch_detail["events"])

    conversation = client.get(f"/api/v1/tasks/{branch['task_id']}/conversation").json()
    assert conversation["branch_origin"] == branch["branch_origin"]
    assert [turn["question"] for turn in conversation["turns"]] == ["假设只看华东区域"]

    family = client.get(f"/api/v1/tasks/{branch['task_id']}/branches")
    assert family.status_code == 200
    payload = family.json()
    assert payload["root_task_id"] == main["task_id"]
    assert [item["task_id"] for item in payload["items"]] == [main["task_id"], branch["task_id"]]
    assert payload["items"][0]["kind"] == "main"
    assert payload["items"][1]["kind"] == "branch"
    assert payload["items"][1]["label"] == "华东假设"
    assert "rows" not in json.dumps(payload, ensure_ascii=False)
    assert "SELECT" not in json.dumps(payload, ensure_ascii=False)


def test_branch_idempotent_replay_preserves_origin_projection():
    client = TestClient(app)
    main = _query(client, "查询区域销售额", "SELECT region, SUM(amount) AS total FROM sales GROUP BY region")
    request = {
        "question": "假设只看华东区域",
        "sql": "SELECT region, SUM(amount) AS total FROM sales WHERE region = :region GROUP BY region",
        "params": {"region": "华东"},
        "branch_from_task_id": main["task_id"],
        "branch_from_trace_id": main["trace_id"],
        "branch_label": "华东假设",
        "client_request_id": "00000000-0000-4000-8000-000000000606",
    }

    created = client.post("/api/v1/query", json=request).json()
    replayed = client.post("/api/v1/query", json=request).json()

    assert replayed["task_id"] == created["task_id"]
    assert replayed["idempotent_replay"] is True
    assert replayed["branch_origin"] == created["branch_origin"]


def test_branch_generation_receives_only_history_through_the_selected_turn(monkeypatch):
    client = TestClient(app)
    first = _query(client, "第一轮销售额", "SELECT amount FROM sales")
    second = client.post(
        "/api/v1/query",
        json={
            "task_id": first["task_id"],
            "follow_up": True,
            "question": "第二轮按区域",
            "sql": "SELECT region, amount FROM sales",
        },
    ).json()
    captured = {}

    async def fake_generate(_question, _schema, **kwargs):
        captured["context"] = kwargs["context"]
        return {"sql": "SELECT amount FROM sales", "confidence": 0.95}

    monkeypatch.setattr("app.main.settings.model_api_key", "branch-test-key")
    monkeypatch.setattr("app.main.generate_sql", fake_generate)
    response = client.post(
        "/api/v1/query",
        json={
            "question": "从第一轮创建另一种分析",
            "branch_from_task_id": first["task_id"],
            "branch_from_trace_id": first["trace_id"],
        },
    )

    assert response.status_code == 200
    assert "error" not in response.json()
    branch_context = captured["context"]["branch_origin"]
    assert branch_context["branch_source"]["trace_id"] == first["trace_id"]
    assert branch_context["compaction"]["source_turn_count"] == 1
    serialized = json.dumps(branch_context, ensure_ascii=False)
    assert second["trace_id"] not in serialized
    assert "第二轮按区域" not in serialized
    assert "SELECT amount" not in serialized


def test_branch_rejects_unknown_turn_cross_owner_and_excessive_depth(monkeypatch):
    client = TestClient(app)
    main = _query(client, "主任务", "SELECT amount FROM sales")

    unknown = client.post(
        "/api/v1/query",
        json={
            "question": "未知轮次",
            "sql": "SELECT amount FROM sales",
            "branch_from_task_id": main["task_id"],
            "branch_from_trace_id": "missing-trace",
        },
    )
    assert unknown.status_code == 409
    assert "E_BRANCH_SOURCE_TURN" in unknown.text

    app.dependency_overrides[require_identity] = lambda: {
        "subject": "other-owner",
        "tenant_id": "default",
        "roles": ["analyst"],
    }
    try:
        forbidden = client.post(
            "/api/v1/query",
            json={
                "question": "跨账号分支",
                "sql": "SELECT amount FROM sales",
                "branch_from_task_id": main["task_id"],
                "branch_from_trace_id": main["trace_id"],
            },
        )
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.pop(require_identity, None)

    first_branch = client.post(
        "/api/v1/query",
        json={
            "question": "一级分支",
            "sql": "SELECT amount FROM sales",
            "branch_from_task_id": main["task_id"],
            "branch_from_trace_id": main["trace_id"],
        },
    ).json()
    monkeypatch.setattr(settings, "analysis_branch_max_depth", 1)
    too_deep = client.post(
        "/api/v1/query",
        json={
            "question": "二级分支",
            "sql": "SELECT amount FROM sales",
            "branch_from_task_id": first_branch["task_id"],
            "branch_from_trace_id": first_branch["trace_id"],
        },
    )
    assert too_deep.status_code == 409
    assert "E_BRANCH_DEPTH" in too_deep.text

