from scripts.verify_configured_datasources import verify_datasource


def test_verify_datasource_requires_health_schema_and_real_rows():
    datasource = {"id": "postgres-main", "backend": "postgres"}
    calls = []

    def execute(_datasource, sql, _params, _max_rows, _offset=0):
        calls.append(sql)
        if "date_trunc" in sql:
            return ["month", "total_amount"], [["2026-07-01", 900], ["2026-08-01", 1200]], False
        return ["region", "amount"], [["华东", 1200]], False

    result = verify_datasource(
        datasource,
        health_check=lambda _datasource: {"ok": True},
        schema_loader=lambda _datasource: {
            "sales": {"id", "region", "amount", "sold_at"},
            "customers": {"id", "name"},
        },
        executor=execute,
    )

    assert result == {
        "id": "postgres-main",
        "backend": "postgres",
        "ok": True,
        "stage": "query",
        "row_count": 1,
        "monthly_bucket_count": 2,
        "truncated": False,
    }
    assert calls == [
        "SELECT region, amount FROM sales ORDER BY id",
        "SELECT date_trunc('month', sold_at) AS month, SUM(amount) AS total_amount FROM sales GROUP BY date_trunc('month', sold_at) ORDER BY month",
    ]


def test_verify_datasource_fails_closed_before_schema_when_health_fails():
    schema_calls = []
    result = verify_datasource(
        {"id": "sqlserver-main", "backend": "sqlserver"},
        health_check=lambda _datasource: {"ok": False, "message": "credential detail"},
        schema_loader=lambda datasource: schema_calls.append(datasource),
    )

    assert result == {
        "id": "sqlserver-main",
        "backend": "sqlserver",
        "ok": False,
        "stage": "health",
    }
    assert schema_calls == []


def test_verify_datasource_rejects_incomplete_business_schema():
    result = verify_datasource(
        {"id": "clickhouse-main", "backend": "clickhouse"},
        health_check=lambda _datasource: {"ok": True},
        schema_loader=lambda _datasource: {"sales": {"id", "region"}},
    )

    assert result["ok"] is False
    assert result["stage"] == "schema"


def test_verify_datasource_requires_a_date_column_for_monthly_analytics():
    result = verify_datasource(
        {"id": "sqlserver-main", "backend": "sqlserver"},
        health_check=lambda _datasource: {"ok": True},
        schema_loader=lambda _datasource: {"sales": {"id", "region", "amount"}},
    )

    assert result == {
        "id": "sqlserver-main",
        "backend": "sqlserver",
        "ok": False,
        "stage": "schema",
    }
