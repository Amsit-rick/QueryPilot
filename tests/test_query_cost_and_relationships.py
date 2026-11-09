import json
import sqlite3
from pathlib import Path

from app.db import schema_details
from app.domain.data_gateway import GatewayCostEstimate, GatewayQuery
from app.gateways.sqlite_gateway import SQLiteGateway
from app.services.query_cost_service import evaluate_query_cost
from app.services.relationship_service import resolve_relationships
from scripts.evaluate_relationship_accuracy import evaluate


def test_sqlite_schema_details_exposes_primary_keys_and_foreign_keys(tmp_path):
    path = tmp_path / "relations.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY); CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id));")
    details = schema_details(str(path))
    assert details["tables"]["customers"]["primary_key"] == ["id"]
    assert details["relationships"] == [{"from_table": "orders", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"], "cardinality": "many_to_one", "confidence": 1.0, "source": "database_metadata"}]


def test_sqlite_schema_details_groups_composite_foreign_keys(tmp_path):
    path = tmp_path / "composite.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE parent(a INTEGER, b INTEGER, PRIMARY KEY(a,b)); CREATE TABLE child(x INTEGER, y INTEGER, FOREIGN KEY(x,y) REFERENCES parent(a,b));")
    relation = schema_details(str(path))["relationships"][0]
    assert relation["from_columns"] == ["x", "y"]
    assert relation["to_columns"] == ["a", "b"]


def test_sqlite_gateway_cost_estimate_uses_explain_without_running_query(tmp_path):
    path = tmp_path / "query.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sales(id INTEGER PRIMARY KEY, amount REAL)")
    gateway = SQLiteGateway(gateway_id="demo", name="Demo", database_path=str(path))
    estimate = gateway.estimate_cost(GatewayQuery("SELECT amount FROM sales WHERE id=:id", params={"id": 1}, max_rows=10))
    assert estimate.supported is True
    assert estimate.plan["rows"]


def test_query_cost_decision_blocks_over_threshold_and_unsupported_when_required():
    blocked = evaluate_query_cost(GatewayCostEstimate("postgres", True, estimated_rows=101, estimated_cost=2), max_estimated_rows=100, max_estimated_cost=10)
    assert blocked.allowed is False and "estimated_rows_limit" in blocked.reasons
    unsupported = evaluate_query_cost(GatewayCostEstimate("oracle", False, reason="missing driver"), max_estimated_rows=100, max_estimated_cost=10, require_explain=True)
    assert unsupported.allowed is False and unsupported.reasons == ("explain_unsupported",)


def test_manual_relationships_are_validated_and_merged_with_database_metadata():
    schema = {"orders": {"id", "customer_code"}, "customers": {"code", "name"}, "payments": {"order_id"}}
    relationships = resolve_relationships(
        schema,
        database=[{"from_table": "payments", "from_columns": ["order_id"], "to_table": "orders", "to_columns": ["id"]}],
        manual=[
            {"from_table": "orders", "from_columns": ["customer_code"], "to_table": "customers", "to_columns": ["code"], "cardinality": "many_to_one", "confidence": 0.92, "version": "sales-v3", "approved": True},
            {"from_table": "orders", "from_columns": ["missing"], "to_table": "customers", "to_columns": ["code"]},
            {"from_table": "orders", "from_columns": ["id"], "to_table": "hidden", "to_columns": ["id"]},
        ],
    )
    assert len(relationships) == 2
    manual = next(item for item in relationships if item["source"] == "manual")
    assert manual["confidence"] == 0.92 and manual["approved"] is True and manual["version"] == "sales-v3"


def test_manual_relationships_reject_mismatched_composite_columns():
    assert resolve_relationships({"a": {"x", "y"}, "b": {"x"}}, manual=[{"from_table": "a", "from_columns": ["x", "y"], "to_table": "b", "to_columns": ["x"]}]) == []


def test_manual_relationships_are_scoped_by_datasource_and_tenant():
    schema = {"orders": {"customer_id"}, "customers": {"id"}}
    manual = [{"from_table": "orders", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"], "datasource_id": "sales", "tenant_id": "tenant-a"}]
    assert len(resolve_relationships(schema, manual=manual, datasource_id="sales", tenant_id="tenant-a")) == 1
    assert resolve_relationships(schema, manual=manual, datasource_id="sales", tenant_id="tenant-b") == []
    assert resolve_relationships(schema, manual=manual, datasource_id="hr", tenant_id="tenant-a") == []


def test_relationship_accuracy_fixture_is_reproducible():
    report = evaluate(Path("data/relationship_eval.json"))
    assert report["total"] == 4
    assert report["passed"] == 4
    assert report["external_validation"] == "not_run"
    risky = next(item for item in report["results"] if item["id"] == "two_many_sides_preaggregate")
    assert risky["business_result_correct"] is True
    assert risky["duplicate_risk_detected"] is True
