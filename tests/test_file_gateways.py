from pathlib import Path

import pytest

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.csv_gateway import CSVGateway
from app.gateways.duckdb_gateway import DuckDBGateway
from app.gateways.factory import build_gateway_registry


def _write_csv(root: Path, name: str = "sales.csv") -> Path:
    path = root / name
    path.write_text("id,region,amount\n1,east,10.50\n2,west,20.25\n", encoding="utf-8")
    return path


def test_csv_gateway_schema_preview_and_query(tmp_path):
    _write_csv(tmp_path)
    gateway = CSVGateway(
        gateway_id="csv-sales",
        name="Sales CSV",
        root_dir=tmp_path,
        file_name="sales.csv",
        dataset_name="sales",
    )

    assert gateway.descriptor.backend == "csv"
    assert gateway.descriptor.dialect == "duckdb"
    assert gateway.health().status == "healthy"
    assert gateway.schema()["sales"] == ["id", "region", "amount"]
    preview = gateway.preview("sales", limit=1, offset=1)
    assert preview.rows == [[2, "west", pytest.approx(20.25)]]
    result = gateway.query(GatewayQuery(sql="SELECT region, amount FROM sales ORDER BY id", max_rows=1))
    assert result.columns == ["region", "amount"]
    assert result.rows == [["east", pytest.approx(10.50)]]
    assert result.truncated is True


@pytest.mark.parametrize("file_name", ["../secret.csv", "sub/../../secret.csv", "C:/secret.csv", "https://example.com/data.csv", "file:///tmp/data.csv"])
def test_file_gateways_reject_paths_outside_configured_root(tmp_path, file_name):
    with pytest.raises(GatewayError) as exc:
        CSVGateway(gateway_id="csv", name="CSV", root_dir=tmp_path, file_name=file_name, dataset_name="sales")
    assert exc.value.code == "E_GATEWAY_PATH"


def test_duckdb_gateway_registers_only_configured_datasets(tmp_path):
    _write_csv(tmp_path, "sales.csv")
    (tmp_path / "customers.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")
    gateway = DuckDBGateway(
        gateway_id="duckdb-files",
        name="File Analytics",
        root_dir=tmp_path,
        datasets={"sales": "sales.csv", "customers": "customers.csv"},
    )

    assert set(gateway.schema()) == {"sales", "customers"}
    result = gateway.query(GatewayQuery(sql="SELECT customers.name, sales.amount FROM customers JOIN sales ON customers.id = sales.id"))
    assert result.rows == [["Alice", pytest.approx(10.50)]]
    with pytest.raises(GatewayError):
        gateway.preview("unknown")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM unknown",
        "SELECT * FROM read_csv_auto('C:/secret.csv')",
        "SELECT * FROM read_csv_auto('https://example.com/data.csv')",
        "SELECT read_text('C:/secret.txt') FROM sales",
        "ATTACH 'C:/secret.duckdb' AS secret",
        "COPY sales TO 'C:/export.csv'",
    ],
)
def test_duckdb_gateway_rejects_unregistered_or_file_access_sql(tmp_path, sql):
    _write_csv(tmp_path)
    gateway = DuckDBGateway(gateway_id="duckdb", name="DuckDB", root_dir=tmp_path, datasets={"sales": "sales.csv"})
    with pytest.raises(GatewayError) as exc:
        gateway.query(GatewayQuery(sql=sql))
    assert exc.value.code == "E_GATEWAY_QUERY"


def test_file_gateway_health_does_not_expose_configured_path(tmp_path):
    secret_root = tmp_path / "private" / "tenant-a"
    gateway = CSVGateway(gateway_id="csv", name="CSV", root_dir=secret_root, file_name="missing.csv", dataset_name="sales")
    health = gateway.health()
    assert health.status == "unavailable"
    assert str(secret_root) not in health.message


def test_factory_registers_csv_and_duckdb_datasources(tmp_path):
    _write_csv(tmp_path)
    (tmp_path / "customers.csv").write_text("id,name\n1,Alice\n", encoding="utf-8")

    registry = build_gateway_registry(
        [
            {
                "id": "csv-sales",
                "backend": "csv",
                "root_dir": str(tmp_path),
                "file_name": "sales.csv",
            },
            {
                "id": "duckdb-files",
                "backend": "duckdb",
                "root_dir": str(tmp_path),
                "datasets": {"sales": "sales.csv", "customers": "customers.csv"},
            },
        ]
    )

    assert registry.get("csv-sales").descriptor.backend == "csv"
    assert registry.get("csv-sales").schema()["sales"] == ["id", "region", "amount"]
    assert set(registry.get("duckdb-files").schema()) == {"sales", "customers"}


def test_factory_reports_missing_file_gateway_configuration(tmp_path):
    with pytest.raises(GatewayError) as exc:
        build_gateway_registry(
            [{"id": "csv-sales", "backend": "csv", "root_dir": str(tmp_path)}]
        )
    assert exc.value.code == "E_GATEWAY_CONFIG"


def test_factory_rejects_duplicate_gateway_ids(tmp_path):
    with pytest.raises(GatewayError, match="数据网关 id 重复") as exc:
        build_gateway_registry(
            [
                {"id": "same", "backend": "sqlite", "path": str(tmp_path / "a.db")},
                {"id": "same", "backend": "sqlite", "path": str(tmp_path / "b.db")},
            ]
        )
    assert exc.value.code == "E_GATEWAY_CONFIG"


def test_factory_rejects_unsupported_backend_instead_of_silently_dropping_it():
    with pytest.raises(GatewayError, match="暂不支持数据网关类型：unsupported") as exc:
        build_gateway_registry([{"id": "unsupported", "backend": "unsupported"}])
    assert exc.value.code == "E_GATEWAY_UNSUPPORTED"
