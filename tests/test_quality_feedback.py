import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agent.contracts import AnalysisMode, DerivedAnalysis
from app.agent.runtime import create_query_graph
from app.agent.state import QueryAgentState
from app.auth import require_identity
from app.main import app
from app.services.feedback_service import contains_credentials, sanitize_question, sanitize_sql_template
from app.services.quality_gate_service import ApprovalGateRequired, anomaly_gate_reasons, evaluate_pre_execution, relationship_gate_reasons


def test_quality_gate_detects_low_confidence_model_risk_and_large_results():
    decision = evaluate_pre_execution(
        "SELECT * FROM sales",
        confidence=0.4,
        model_requires_approval=True,
        requested_rows=9000,
        low_confidence_threshold=0.65,
        high_risk_row_threshold=5000,
    )
    assert decision.required is True
    assert decision.reasons == ("model_requested", "low_confidence", "wildcard_projection", "large_result_request")
    assert decision.confidence == 0.4


def test_unknown_confidence_is_not_misrepresented_as_low_confidence():
    decision = evaluate_pre_execution(
        "SELECT region FROM sales",
        confidence=0.0,
        model_requires_approval=False,
        requested_rows=100,
        low_confidence_threshold=0.65,
        high_risk_row_threshold=5000,
    )
    assert decision.required is False
    assert decision.confidence is None


def test_count_star_is_not_treated_as_wildcard_projection():
    decision = evaluate_pre_execution(
        "SELECT COUNT(*) AS row_count FROM sales",
        confidence=0.9,
        model_requires_approval=False,
        requested_rows=100,
        low_confidence_threshold=0.65,
        high_risk_row_threshold=5000,
    )
    assert decision.required is False


def test_relationship_gate_requires_confirmation_for_unapproved_manual_join():
    relationships = [{"from_table": "orders", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"], "confidence": 0.92, "approved": False}]
    assert relationship_gate_reasons("SELECT o.id, c.name FROM orders o JOIN customers c ON c.id=o.customer_id", relationships) == ("relationship_confirmation",)
    approved = [{**relationships[0], "approved": True}]
    assert relationship_gate_reasons("SELECT o.id, c.name FROM orders o JOIN customers c ON c.id=o.customer_id", approved) == ()
    low = [{**approved[0], "confidence": 0.4}]
    assert relationship_gate_reasons("SELECT o.id, c.name FROM orders o JOIN customers c ON c.id=o.customer_id", low) == ("relationship_confirmation",)


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_agent_runtime_stops_before_execution_for_low_confidence(runtime):
    executions = []

    async def generate(_question):
        return {"sql": "SELECT region FROM sales", "confidence": 0.2, "needs_approval": False}

    def gate(sql, confidence, needs_approval, analysis_plan):
        decision = evaluate_pre_execution(
            sql,
            confidence=confidence,
            model_requires_approval=needs_approval,
            requested_rows=100,
            low_confidence_threshold=0.65,
            high_risk_row_threshold=5000,
        )
        if decision.required:
            raise ApprovalGateRequired(sql=sql, reasons=decision.reasons, confidence=decision.confidence, analysis_mode=analysis_plan["analysis_mode"])

    graph = create_query_graph(
        runtime=runtime,
        generate=generate,
        validate=lambda sql: sql,
        execute=lambda *_: executions.append(True),
        pre_execute=gate,
    )
    with pytest.raises(ApprovalGateRequired) as caught:
        asyncio.run(graph.ainvoke(QueryAgentState("查询区域")))
    assert caught.value.reasons == ("low_confidence",)
    assert executions == []


def test_candidate_anomaly_is_an_approval_reason():
    assert anomaly_gate_reasons({"anomalies": [{"column": "amount"}]}) == ("candidate_anomaly",)
    assert anomaly_gate_reasons({"anomalies": []}) == ()


def test_wildcard_query_automatically_enters_existing_approval_workflow():
    app.dependency_overrides[require_identity] = lambda: {"subject": "admin", "tenant_id": "default", "roles": ["admin"]}
    try:
        client = TestClient(app)
        pending = client.post("/api/v1/query", json={"question": "查看全部销售字段", "sql": "SELECT * FROM sales"})
        assert pending.status_code == 200
        body = pending.json()
        assert body["status"] == "approval_required"
        assert body["approval_trigger"] == "automatic"
        assert body["approval_reasons"] == ["wildcard_projection"]
        detail = client.get(f"/api/v1/tasks/{body['task_id']}").json()
        assert detail["status"] == "approval_required"
        approval_step = next(step for step in detail["steps"] if step["kind"] == "approval")
        assert approval_step["output"]["reasons"] == ["wildcard_projection"]
    finally:
        app.dependency_overrides.clear()


def test_candidate_anomaly_is_withheld_until_approval(monkeypatch):
    app.dependency_overrides[require_identity] = lambda: {"subject": "admin", "tenant_id": "default", "roles": ["admin"]}

    def anomaly_result(_output, _plan):
        return DerivedAnalysis(
            mode=AnalysisMode.ANOMALY_DETECTION,
            summary="识别到 1 个候选异常点。",
            time_column="sold_at",
            anomalies=[{
                "column": "amount",
                "period": "2026-01-04",
                "value": 999,
                "baseline": 10,
                "score": 9.9,
                "method": "mad",
            }],
        )

    monkeypatch.setattr("app.main.describe_result", anomaly_result)
    try:
        client = TestClient(app)
        pending = client.post("/api/v1/query", json={
            "question": "检测销售额异常",
            "sql": "SELECT sold_at, amount FROM sales ORDER BY sold_at",
            "analysis_mode": "anomaly_detection",
        }).json()
        assert pending["status"] == "approval_required"
        assert pending["approval_reasons"] == ["candidate_anomaly"]
        before = client.get(f"/api/v1/tasks/{pending['task_id']}").json()
        assert not any(step["kind"] == "analyze_result" for step in before["steps"])
        assert not any(artifact["kind"] == "table" for artifact in before["artifacts"])

        approved = client.post(f"/api/v1/approvals/{pending['approval_id']}/approve")
        assert approved.status_code == 200
        assert approved.json()["analysis"]["anomalies"][0]["column"] == "amount"
        after = client.get(f"/api/v1/tasks/{pending['task_id']}").json()
        assert after["status"] == "completed"
        assert any(step["kind"] == "analyze_result" for step in after["steps"])
        assert any(artifact["kind"] == "table" for artifact in after["artifacts"])
    finally:
        app.dependency_overrides.clear()


def test_feedback_creates_sanitized_offline_case_without_result_rows():
    client = TestClient(app)
    completed = client.post("/api/v1/query", json={
        "question": "查询 alice@example.com 的销售区域",
        "sql": "SELECT region FROM sales WHERE region = 'east'",
    }).json()
    response = client.post(f"/api/v1/tasks/{completed['task_id']}/feedback", json={"outcome": "accepted", "note": "结果符合预期"})
    assert response.status_code == 200
    case = response.json()["offline_case"]
    assert case["question"] == "查询 [REDACTED_EMAIL] 的销售区域"
    assert case["expected_sql_template"] == "SELECT region FROM sales WHERE region = '[REDACTED]'"
    assert case["contains_result_rows"] is False
    assert "rows" not in case

    listed = client.get(f"/api/v1/tasks/{completed['task_id']}/feedback").json()
    assert listed["feedback"][0]["payload"]["outcome"] == "accepted"
    assert listed["offline_cases"][0]["metadata"]["feedback_id"] == case["feedback_id"]

    corrected = client.post(f"/api/v1/tasks/{completed['task_id']}/feedback", json={
        "outcome": "corrected",
        "note": "按区域汇总",
        "corrected_sql": "SELECT region, SUM(amount) FROM sales GROUP BY region",
    })
    assert corrected.status_code == 200
    assert corrected.json()["offline_case"]["expected_sql_template"] == "SELECT region, SUM(amount) FROM sales GROUP BY region"
    rejected = client.post(f"/api/v1/tasks/{completed['task_id']}/feedback", json={"outcome": "rejected"})
    assert rejected.status_code == 200
    assert rejected.json()["outcome"] == "rejected"


def test_feedback_contract_rejects_result_rows():
    client = TestClient(app)
    task_id = client.post("/api/v1/query", json={"question": "查询区域", "sql": "SELECT region FROM sales"}).json()["task_id"]
    response = client.post(f"/api/v1/tasks/{task_id}/feedback", json={"outcome": "accepted", "rows": [["east"]]})
    assert response.status_code == 422


def test_feedback_rejects_unsafe_correction_and_credentials():
    client = TestClient(app)
    task_id = client.post("/api/v1/query", json={"question": "查询销售区域", "sql": "SELECT region FROM sales"}).json()["task_id"]
    unsafe = client.post(f"/api/v1/tasks/{task_id}/feedback", json={"outcome": "corrected", "corrected_sql": "DELETE FROM sales"})
    assert unsafe.status_code == 422
    assert unsafe.json()["detail"]["code"] == "E_READONLY_VIOLATION"
    secret = client.post(f"/api/v1/tasks/{task_id}/feedback", json={"outcome": "rejected", "note": "api_key=top-secret-value"})
    assert secret.status_code == 409
    assert secret.json()["detail"]["code"] == "E_FEEDBACK_SECRET"


def test_failed_feedback_requires_a_failed_owned_task():
    client = TestClient(app)
    failed = client.post("/api/v1/query", json={"question": "失败查询", "sql": "DELETE FROM sales"}).json()
    recorded = client.post(f"/api/v1/tasks/{failed['task_id']}/feedback", json={"outcome": "failed", "note": "只读校验失败"})
    assert recorded.status_code == 200
    assert recorded.json()["offline_case"]["error_code"] == "E_READONLY_VIOLATION"


def test_feedback_isolated_by_task_owner():
    current = {"subject": "owner-a", "tenant_id": "tenant-a", "roles": ["analyst"]}
    app.dependency_overrides[require_identity] = lambda: current
    try:
        client = TestClient(app)
        task_id = client.post("/api/v1/query", json={"question": "查询区域", "sql": "SELECT region FROM sales"}).json()["task_id"]
        current = {"subject": "owner-b", "tenant_id": "tenant-a", "roles": ["analyst"]}
        assert client.post(f"/api/v1/tasks/{task_id}/feedback", json={"outcome": "accepted"}).status_code == 404
        assert client.get(f"/api/v1/tasks/{task_id}/feedback").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_feedback_sanitizers_remove_credentials_and_literals():
    assert contains_credentials("password=hunter2") is True
    assert "hunter2" not in sanitize_question("password=hunter2")
    template = sanitize_sql_template("SELECT amount FROM sales WHERE region='east' AND amount > 100 LIMIT 10")
    assert "east" not in template
    assert "100" not in template
    assert "LIMIT 10" in template


def test_only_admin_approved_feedback_enters_versioned_regression_dataset():
    current = {"subject": "analyst-1", "tenant_id": "tenant-quality", "roles": ["analyst"]}
    app.dependency_overrides[require_identity] = lambda: current
    try:
        client = TestClient(app)
        task_id = client.post(
            "/api/v1/query",
            json={"question": "查询销售区域", "sql": "SELECT region FROM sales"},
        ).json()["task_id"]
        draft = client.post(
            f"/api/v1/tasks/{task_id}/feedback",
            json={"outcome": "accepted", "note": "结果正确"},
        ).json()

        denied = client.post(
            f"/api/v1/quality/admin/tasks/{task_id}/feedback/{draft['feedback_id']}/review",
            json={"decision": "approved", "dataset_version": "feedback-regression-v1"},
        )
        assert denied.status_code == 403

        current = {"subject": "quality-admin", "tenant_id": "tenant-quality", "roles": ["admin"]}
        approved = client.post(
            f"/api/v1/quality/admin/tasks/{task_id}/feedback/{draft['feedback_id']}/review",
            json={"decision": "approved", "dataset_version": "feedback-regression-v1", "note": "纳入回归"},
        )
        assert approved.status_code == 200
        assert approved.json()["review_status"] == "approved"
        assert approved.json()["reviewer_id"] == "quality-admin"

        dataset = client.get(
            "/api/v1/quality/admin/evaluation-datasets/feedback-regression-v1"
        )
        assert dataset.status_code == 200
        payload = dataset.json()
        assert payload["format"] == "query-pilot-feedback-eval-dataset"
        assert payload["dataset_version"] == "feedback-regression-v1"
        assert payload["case_count"] == 1
        assert payload["cases"][0]["feedback_id"] == draft["feedback_id"]
        assert payload["cases"][0]["contains_result_rows"] is False
        assert payload["cases"][0]["expected_sql_contract"]["tables"] == ["sales"]
        assert payload["cases"][0]["expected_result_contract"]["rows_sha256"]
        assert "rows" not in payload["cases"][0]["expected_result_contract"]
        assert payload["cases"][0]["source_versions"]["model"]
        assert payload["cases"][0]["source_versions"]["prompt"]
    finally:
        app.dependency_overrides.clear()


def test_rejected_feedback_never_enters_approved_dataset():
    current = {"subject": "analyst-2", "tenant_id": "tenant-quality-rejected", "roles": ["analyst"]}
    app.dependency_overrides[require_identity] = lambda: current
    try:
        client = TestClient(app)
        task_id = client.post(
            "/api/v1/query",
            json={"question": "查询区域", "sql": "SELECT region FROM sales"},
        ).json()["task_id"]
        feedback_id = client.post(
            f"/api/v1/tasks/{task_id}/feedback",
            json={"outcome": "rejected", "note": "问题口径不清"},
        ).json()["feedback_id"]
        current = {"subject": "quality-admin", "tenant_id": "tenant-quality-rejected", "roles": ["admin"]}
        review = client.post(
            f"/api/v1/quality/admin/tasks/{task_id}/feedback/{feedback_id}/review",
            json={"decision": "rejected", "note": "不适合作为回归样例"},
        )
        assert review.status_code == 200
        dataset = client.get("/api/v1/quality/admin/evaluation-datasets/feedback-regression-v1").json()
        assert dataset["case_count"] == 0
    finally:
        app.dependency_overrides.clear()
