from app.gateways.registry import GatewayRegistry
from app.gateways.sqlite_gateway import SQLiteGateway
from app.gateways.mysql_gateway import MySQLGateway
from app.gateways.postgres_gateway import PostgreSQLGateway
from app.gateways.csv_gateway import CSVGateway
from app.gateways.duckdb_gateway import DuckDBGateway
from app.domain.data_gateway import GatewayError
from app.gateways.oracle_gateway import OracleGateway
from app.gateways.sqlserver_gateway import SQLServerGateway
from app.gateways.clickhouse_gateway import ClickHouseGateway


REMOTE_GATEWAYS = {"oracle": OracleGateway, "sqlserver": SQLServerGateway, "clickhouse": ClickHouseGateway}
SUPPORTED_GATEWAY_BACKENDS = frozenset({"sqlite", "mysql", "postgres", "postgresql", "csv", "duckdb", *REMOTE_GATEWAYS})


def build_gateway_registry(datasources):
    registry = GatewayRegistry()
    for datasource in datasources:
        backend = str(datasource.get("backend", "sqlite")).lower()
        gateway_id = datasource.get("id")
        if not gateway_id:
            raise GatewayError("E_GATEWAY_CONFIG", "数据网关缺少 id", operation="configure")
        common = {"gateway_id": gateway_id, "name": datasource.get("name", gateway_id)}
        try:
            if backend == "sqlite":
                gateway = SQLiteGateway(
                    **common, database_path=datasource.get("path", "./data/demo.db")
                )
            elif backend == "mysql":
                gateway = MySQLGateway(
                    **common, allowed_tables=datasource.get("allowed_tables")
                )
            elif backend in {"postgres", "postgresql"}:
                gateway = PostgreSQLGateway(
                    **common, allowed_tables=datasource.get("allowed_tables")
                )
            elif backend in REMOTE_GATEWAYS:
                remote_options = {"allowed_tables": datasource.get("allowed_tables")}
                if backend == "oracle" and "schema_owner" in datasource:
                    remote_options["schema_owner"] = datasource.get("schema_owner")
                if backend == "oracle" and "cost_estimate_enabled" in datasource:
                    remote_options["cost_estimate_enabled"] = datasource.get("cost_estimate_enabled")
                gateway = REMOTE_GATEWAYS[backend](**common, **remote_options)
            elif backend == "csv":
                gateway = CSVGateway(
                    **common,
                    root_dir=datasource["root_dir"],
                    file_name=datasource["file_name"],
                    dataset_name=datasource.get("dataset_name")
                    or str(datasource["file_name"]).rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0],
                )
            elif backend == "duckdb":
                gateway = DuckDBGateway(
                    **common,
                    root_dir=datasource["root_dir"],
                    datasets=datasource["datasets"],
                )
            else:
                raise GatewayError(
                    "E_GATEWAY_UNSUPPORTED",
                    f"暂不支持数据网关类型：{backend}",
                    operation="configure",
                )
        except KeyError as exc:
            raise GatewayError(
                "E_GATEWAY_CONFIG",
                f"{backend} 数据网关缺少配置字段",
                operation="configure",
            ) from exc
        try:
            registry.register(gateway)
        except ValueError as exc:
            raise GatewayError("E_GATEWAY_CONFIG", "数据网关 id 重复", operation="configure") from exc
    return registry
