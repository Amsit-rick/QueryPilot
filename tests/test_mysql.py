import os
import pytest
pytest.importorskip("pymysql")

@pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="set RUN_MYSQL_TESTS=1 to run against local MySQL")
def test_mysql_connection():
    from app.mysql_db import mysql_schema, execute_mysql
    schema = mysql_schema()
    assert {"sales", "customers"} <= set(schema)
    columns, rows, truncated = execute_mysql("SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY total_amount DESC", {}, 10)
    assert columns == ["region", "total_amount"]
    assert 0 < len(rows) <= 10 and all(rows[i][1] >= rows[i + 1][1] for i in range(len(rows) - 1))
    assert truncated is False

@pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="set RUN_MYSQL_TESTS=1 to run against local MySQL")
def test_mysql_sql_with_percent_literal():
    from app.mysql_db import execute_mysql
    columns, rows, _ = execute_mysql("SELECT DATE_FORMAT(sold_at, '%Y-%m') AS month, SUM(amount) AS total FROM sales GROUP BY month ORDER BY month LIMIT 12", {}, 12)
    assert columns == ["month", "total"] and 0 < len(rows) <= 12


@pytest.mark.skipif(os.getenv("RUN_MYSQL_READONLY_TESTS") != "1", reason="set RUN_MYSQL_READONLY_TESTS=1 to verify the read-only account")
def test_mysql_readonly_account_rejects_writes():
    import pymysql

    connection = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        user=os.environ["MYSQL_READONLY_USER"],
        password=os.environ["MYSQL_READONLY_PASSWORD"],
        database=os.getenv("MYSQL_DATABASE", "query_agent"),
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sales")
            assert cursor.fetchone()[0] > 0
            with pytest.raises(pymysql.err.OperationalError):
                cursor.execute("CREATE TABLE forbidden_write(id BIGINT)")
    finally:
        connection.close()


@pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="set RUN_MYSQL_TESTS=1 to run against local MySQL")
def test_mysql_approval_completion_persists_linked_task(monkeypatch):
    from fastapi.testclient import TestClient
    from app.auth import require_identity
    from app.config import settings
    from app.main import app

    identity = {"subject": "integration-admin", "tenant_id": "integration", "roles": ["admin"]}
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "database_backend", "mysql")
    app.dependency_overrides[require_identity] = lambda: identity
    try:
        client = TestClient(app, headers={"Authorization": "Bearer integration-test"})
        pending = client.post("/api/v1/query", json={"question": "审批持久化验证", "sql": "SELECT region FROM sales", "require_approval": True}).json()
        approved = client.post(f"/api/v1/approvals/{pending['approval_id']}/approve")
        assert approved.status_code == 200
        detail = client.get(f"/api/v1/tasks/{pending['task_id']}").json()
        assert detail["status"] == "completed"
        assert next(step for step in detail["steps"] if step["kind"] == "approval")["status"] == "completed"
        assert len(detail["artifacts"]) == 1
        assert detail["artifacts"][0]["metadata"]["rows"]
    finally:
        app.dependency_overrides.clear()
