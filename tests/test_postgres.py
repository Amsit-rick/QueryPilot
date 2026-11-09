import os

import pytest

pytest.importorskip("psycopg")


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="set RUN_POSTGRES_TESTS=1 to run against local PostgreSQL")
def test_postgres_readonly_join_and_schema():
    from app.postgres_db import execute_postgres, postgres_schema
    from app.services.analysis_service import describe_result

    assert {"sales", "customers"} <= set(postgres_schema())
    columns, rows, truncated = execute_postgres(
        "SELECT c.name, c.region, SUM(s.amount) AS total_sales "
        "FROM customers c JOIN sales s ON c.region = s.region "
        "GROUP BY c.name, c.region ORDER BY total_sales DESC",
        {},
        10,
    )
    assert columns == ["name", "region", "total_sales"]
    assert len(rows) == 2
    assert truncated is False
    analysis = describe_result((columns, rows, truncated), {"analysis_mode": "descriptive_statistics"})
    assert analysis.metrics["total_sales"]["count"] == 2
