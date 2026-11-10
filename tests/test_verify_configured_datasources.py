from scripts.verify_configured_datasources import verify_datasource


def test_verify_datasource_requires_health_schema_and_real_rows():
    datasource = {"id": "postgres-main", "backend": "postgres"}

    result = verify_datasource(
        datasource,
        health_check=lambda _datasource: {"ok": True},
        schema_loader=lambda _datasource: {
            "sales": {"id", "region", "amount"},
            "customers": {"id", "name"},
        },
        executor=lambda *_args: (["region", "amount"], [["华东", 1200]], False),
    )

    assert result == {
        "id": "postgres-main",
        "backend": "postgres",
        "ok": True,
        "stage": "query",
        "row_count": 1,
        "truncated": False,
    }


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
