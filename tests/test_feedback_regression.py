import asyncio

from fastapi.testclient import TestClient

from app.auth import require_identity
from app.feedback_regression import (
    build_result_contract,
    build_sql_contract,
    evaluate_feedback_regression,
)
from app.main import app


SCHEMA = {
    "sales": {"id", "region", "amount", "sold_at"},
    "customers": {"id", "name", "region"},
}


def _dataset(*cases):
    return {
        "format": "query-pilot-feedback-eval-dataset",
        "version": 1,
        "dataset_version": "feedback-regression-v1",
        "cases": list(cases),
    }


def _case(**overrides):
    expected_sql = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region"
    payload = {
        "feedback_id": "feedback-1",
        "review_status": "approved",
        "question": "按区域汇总销售额",
        "datasource_id": "sqlite-demo",
        "expected_sql_template": expected_sql,
        "expected_sql_contract": build_sql_contract(expected_sql),
        "expected_result_contract": {},
    }
    payload.update(overrides)
    return payload


def test_feedback_regression_runs_only_approved_cases_and_passes_structure_match():
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs["question"])
        return {
            "sql": "SELECT region, SUM(amount) AS sales_total FROM sales GROUP BY region",
            "params": {},
        }

    report = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(
            _case(),
            _case(feedback_id="draft-1", review_status="draft", question="不应执行"),
            _case(feedback_id="rejected-1", review_status="rejected", question="也不应执行"),
        ),
        generate=generate,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
        model={"provider": "fixture", "model": "fixture-model", "prompt_version": "fixture-v1"},
    ))

    assert calls == ["按区域汇总销售额"]
    assert report["external_validation"] == "passed"
    assert report["gate"]["passed"] is True
    assert report["metrics"]["approved_cases"] == 1
    assert report["metrics"]["ignored_unapproved_cases"] == 2
    assert report["cases"][0] == {
        "feedback_id": "feedback-1",
        "status": "passed",
        "error_code": "",
        "structure_match": True,
        "result_match": None,
    }
    assert "generated_sql" not in report["cases"][0]


def test_feedback_regression_is_not_run_without_current_model_configuration():
    report = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(_case()),
        generate=None,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
        model={"provider": "environment", "model": "deepseek-chat", "prompt_version": "builtin"},
    ))

    assert report["external_validation"] == "not_run"
    assert report["gate"] == {"passed": False, "reason_codes": ["MODEL_NOT_CONFIGURED"]}
    assert report["cases"][0]["status"] == "not_run"
    assert report["cases"][0]["error_code"] == "E_MODEL_NOT_CONFIGURED"


def test_feedback_regression_fails_closed_on_generation_guardrail_and_structure_errors():
    async def malformed(**_kwargs):
        raise RuntimeError("provider payload leaked-value")

    generation = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(_case()),
        generate=malformed,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
    ))
    assert generation["gate"]["passed"] is False
    assert generation["cases"][0]["error_code"] == "E_MODEL_GENERATION"
    assert "leaked-value" not in str(generation)

    async def unsafe(**_kwargs):
        return {"sql": "DELETE FROM sales", "params": {}}

    guardrail = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(_case()),
        generate=unsafe,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
    ))
    assert guardrail["cases"][0]["status"] == "rejected"
    assert guardrail["cases"][0]["error_code"] == "E_READONLY_VIOLATION"

    async def wrong_table(**_kwargs):
        return {"sql": "SELECT region, COUNT(id) FROM customers GROUP BY region", "params": {}}

    mismatch = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(_case()),
        generate=wrong_table,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
    ))
    assert mismatch["cases"][0]["error_code"] == "E_SQL_STRUCTURE_MISMATCH"
    assert mismatch["cases"][0]["structure_match"] is False


def test_feedback_regression_compares_reproducible_gold_result_digest():
    expected = build_result_contract(
        ["region", "total"],
        [["华东", 1960.0], ["华南", 980.0]],
        truncated=False,
    )

    async def generate(**_kwargs):
        return {
            "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region",
            "params": {},
        }

    def execute(_case, _sql, _params):
        return ["region", "total"], [["华东", 1960.0], ["华南", 980.0]], False

    passed = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(_case(expected_result_contract=expected)),
        generate=generate,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
        execute=execute,
    ))
    assert passed["cases"][0]["result_match"] is True
    assert passed["gate"]["passed"] is True

    def wrong_result(_case, _sql, _params):
        return ["region", "total"], [["华东", 1.0]], False

    failed = asyncio.run(evaluate_feedback_regression(
        dataset=_dataset(_case(expected_result_contract=expected)),
        generate=generate,
        schema_for=lambda _case: SCHEMA,
        dialect_for=lambda _case: "sqlite",
        execute=wrong_result,
    ))
    assert failed["cases"][0]["result_match"] is False
    assert failed["cases"][0]["error_code"] == "E_RESULT_MISMATCH"
    assert failed["gate"]["passed"] is False


def test_sql_contract_ignores_aliases_and_literals_but_tracks_query_shape():
    left = build_sql_contract(
        "SELECT region, SUM(amount) AS total FROM sales WHERE sold_at >= '2026-01-01' GROUP BY region"
    )
    right = build_sql_contract(
        "SELECT region, SUM(amount) AS sales_total FROM sales WHERE sold_at >= :start_date GROUP BY region"
    )
    assert left == right
    assert left["tables"] == ["sales"]
    assert left["columns"] == ["amount", "region", "sold_at"]
    assert left["aggregates"] == ["SUM"]


def test_admin_feedback_regression_endpoint_marks_missing_model_as_not_run(monkeypatch):
    current = {"subject": "analyst-regression", "tenant_id": "tenant-regression", "roles": ["analyst"]}
    app.dependency_overrides[require_identity] = lambda: current
    try:
        client = TestClient(app)
        task = client.post(
            "/api/v1/query",
            json={"question": "查询销售区域", "sql": "SELECT region FROM sales"},
        ).json()
        feedback = client.post(
            f"/api/v1/tasks/{task['task_id']}/feedback",
            json={"outcome": "accepted"},
        ).json()
        current = {"subject": "quality-admin", "tenant_id": "tenant-regression", "roles": ["admin"]}
        approved = client.post(
            f"/api/v1/quality/admin/tasks/{task['task_id']}/feedback/{feedback['feedback_id']}/review",
            json={"decision": "approved", "dataset_version": "feedback-regression-v1"},
        )
        assert approved.status_code == 200
        monkeypatch.setattr("app.config.settings.model_api_key", "")
        response = client.post(
            "/api/v1/quality/admin/evaluation-datasets/feedback-regression-v1/regressions"
        )
        assert response.status_code == 200
        report = response.json()
        assert report["external_validation"] == "not_run"
        assert report["gate"]["passed"] is False
        assert report["gate"]["reason_codes"] == ["MODEL_NOT_CONFIGURED"]
    finally:
        app.dependency_overrides.clear()
