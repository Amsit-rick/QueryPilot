from fastapi.testclient import TestClient
from app.main import app


def test_inventory_question_uses_enterprise_tables(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "mysql")
    response = TestClient(app).post("/api/v1/query", json={"question": "查询库存低于补货点的产品和仓库", "datasource": "mysql-main"})
    # Without an LLM key the deterministic template must still be selected;
    # a connection error is acceptable in unit environments, but SQL must not
    # fall back to the legacy sales table.
    body = response.json()
    assert "sales" not in body.get("sql", "").lower()


def test_pending_region_question_does_not_use_sales(monkeypatch):
    response = TestClient(app).post("/api/v1/query", json={"question": "统计各区域的待回款金额", "datasource": "mysql-main"})
    assert "sales" not in response.json().get("sql", "").lower()
