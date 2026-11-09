"""Run a deterministic 1,500-table schema retrieval benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.schema_retrieval_service import retrieve_schema


FORMAT = "query-pilot-schema-retrieval-eval"
VERSION = 1
DEFAULT_DATASET = PROJECT_ROOT / "data" / "schema_retrieval_eval.json"


def build_fixture() -> tuple[dict[str, set[str]], dict, list[dict]]:
    schema = {
        f"archive_event_{index:04d}": {"id", "tenant_id", "created_at", *(f"attribute_{column}" for column in range(9))}
        for index in range(1491)
    }
    schema.update(
        {
            "customers": {"id", "name", "region_id"},
            "regions": {"id", "name"},
            "orders": {"id", "customer_id", "status", "total_amount", "created_at"},
            "order_items": {"order_id", "product_id", "quantity", "unit_price"},
            "products": {"id", "name", "category_id"},
            "categories": {"id", "name"},
            "product_suppliers": {"product_id", "supplier_id"},
            "suppliers": {"id", "name"},
            "payments": {"id", "order_id", "status", "amount"},
        }
    )
    details = {"tables": {table: {"primary_key": ["id"] if "id" in columns else []} for table, columns in schema.items()}}
    relationships = [
        relation("customers", ["region_id"], "regions", ["id"]),
        relation("orders", ["customer_id"], "customers", ["id"]),
        relation("order_items", ["order_id"], "orders", ["id"]),
        relation("order_items", ["product_id"], "products", ["id"]),
        relation("products", ["category_id"], "categories", ["id"]),
        relation("product_suppliers", ["product_id"], "products", ["id"]),
        relation("product_suppliers", ["supplier_id"], "suppliers", ["id"]),
        relation("payments", ["order_id"], "orders", ["id"]),
    ]
    return schema, details, relationships


def relation(from_table: str, from_columns: list[str], to_table: str, to_columns: list[str]) -> dict:
    return {
        "from_table": from_table,
        "from_columns": from_columns,
        "to_table": to_table,
        "to_columns": to_columns,
        "cardinality": "many_to_one",
        "confidence": 1.0,
        "source": "database_metadata",
        "approved": True,
    }


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percent))))
    return ordered[index]


def evaluate(iterations: int = 5, dataset_path: Path = DEFAULT_DATASET) -> dict:
    schema, details, relationships = build_fixture()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    latencies: list[float] = []
    table_hits = table_total = column_hits = column_total = path_hits = 0
    reductions: list[float] = []
    case_reports = []
    source_recall: dict[str, list[float]] = {}
    source_mrr: dict[str, list[float]] = {}
    embedding_statuses: list[str] = []
    for case in cases:
        result = None
        semantics = [{"definition": {"tables": case["semantic_tables"], "columns": case["semantic_columns"]}}]
        for _ in range(max(1, iterations)):
            started = perf_counter()
            result = retrieve_schema(
                case["question"],
                schema,
                relationships=relationships,
                semantics=semantics,
                schema_details=details,
                table_threshold=50,
                max_tables=20,
                max_columns_per_table=40,
                max_hops=3,
            )
            latencies.append((perf_counter() - started) * 1000)
        selected = set(result.selected_tables)
        expected_tables = set(case["expected_tables"])
        current_table_hits = len(expected_tables & selected)
        table_hits += current_table_hits
        table_total += len(expected_tables)
        current_column_hits = 0
        for qualified in case["expected_columns"]:
            table, column = qualified.split(".", 1)
            current_column_hits += column in result.schema.get(table, [])
        column_hits += current_column_hits
        column_total += len(case["expected_columns"])
        path_ok = any(set(item["tables"]) == set(case["expected_path"]) for item in result.join_paths)
        path_hits += int(path_ok)
        reductions.append(result.retrieval_stats["schema_reduction_ratio"])
        embedding_statuses.append(result.retrieval_stats.get("embedding", {}).get("status", "disabled"))
        case_source_metrics = {}
        for source, ranking in result.retrieval_stats.get("source_rankings", {}).items():
            ranks = {table: index for index, table in enumerate(ranking, start=1)}
            recall = len(expected_tables & set(ranking)) / len(expected_tables)
            reciprocal_ranks = [1 / ranks[table] if table in ranks else 0.0 for table in expected_tables]
            mean_reciprocal_rank = sum(reciprocal_ranks) / len(reciprocal_ranks)
            source_recall.setdefault(source, []).append(recall)
            source_mrr.setdefault(source, []).append(mean_reciprocal_rank)
            case_source_metrics[source] = {
                "recall_at_20": round(recall, 6),
                "mrr": round(mean_reciprocal_rank, 6),
            }
        case_reports.append(
            {
                "id": case["id"],
                "selected_tables": list(result.selected_tables),
                "table_recall": current_table_hits / len(expected_tables),
                "column_recall": current_column_hits / len(case["expected_columns"]),
                "join_path_correct": path_ok,
                "source_metrics": case_source_metrics,
            }
        )

    ambiguous_schema = {
        **{f"noise_{index}": {"id"} for index in range(1496)},
        "customers": {"id"},
        "orders": {"id"},
        "customer_order_map": {"customer_id", "order_id"},
        "legacy_customer_order_map": {"customer_id", "order_id"},
    }
    ambiguous_relationships = []
    for bridge in ("customer_order_map", "legacy_customer_order_map"):
        ambiguous_relationships.extend(
            [
                relation(bridge, ["customer_id"], "customers", ["id"]),
                relation(bridge, ["order_id"], "orders", ["id"]),
            ]
        )
    ambiguous = retrieve_schema(
        "客户订单映射",
        ambiguous_schema,
        relationships=ambiguous_relationships,
        semantics=[{"definition": {"tables": ["customers", "orders"], "columns": []}}],
        table_threshold=50,
        max_hops=2,
    )
    permission_result = retrieve_schema(
        "payroll salary",
        {"orders": {"id", "amount"}, **{f"noise_{index}": {"id"} for index in range(1499)}},
        relationships=[relation("orders", ["id"], "payroll", ["order_id"])],
        semantics=[{"definition": {"tables": ["payroll"], "columns": ["salary"]}}],
        table_threshold=50,
    )
    permission_payload = str(permission_result).lower()
    unauthorized_leaks = sum(term in permission_payload for term in {"payroll", "salary"})
    metrics = {
        "table_recall_at_20": round(table_hits / table_total, 6),
        "column_recall_at_40": round(column_hits / column_total, 6),
        "join_path_accuracy": round(path_hits / len(cases), 6),
        "ambiguous_path_detection": bool(ambiguous.ambiguous_paths),
        "unauthorized_metadata_leaks": unauthorized_leaks,
        "p50_ms": round(median(latencies), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "mean_schema_reduction_ratio": round(sum(reductions) / len(reductions), 6),
        "source_metrics": {
            source: {
                "recall_at_20": round(sum(values) / len(values), 6),
                "mrr": round(sum(source_mrr[source]) / len(source_mrr[source]), 6),
            }
            for source, values in sorted(source_recall.items())
        },
        "embedding_status": "available" if "available" in embedding_statuses else (
            "degraded" if "degraded" in embedding_statuses else "disabled"
        ),
    }
    fused_recall = metrics["source_metrics"].get("fused", {}).get("recall_at_20", 0.0)
    source_baselines = [
        value["recall_at_20"]
        for source, value in metrics["source_metrics"].items()
        if source != "fused"
    ]
    metrics["fusion_gain_over_best_source"] = round(fused_recall - max(source_baselines or [0.0]), 6)
    graph_recall = metrics["source_metrics"].get("graph_augmented", {}).get("recall_at_20", 0.0)
    metrics["graph_gain_over_fused"] = round(graph_recall - fused_recall, 6)
    thresholds = {
        "table_recall_at_20_min": 0.95,
        "column_recall_at_40_min": 0.95,
        "join_path_accuracy_min": 1.0,
        "p95_ms_max": 1000.0,
        "mean_schema_reduction_ratio_min": 0.95,
        "unauthorized_metadata_leaks_max": 0,
    }
    passed = (
        metrics["table_recall_at_20"] >= thresholds["table_recall_at_20_min"]
        and metrics["column_recall_at_40"] >= thresholds["column_recall_at_40_min"]
        and metrics["join_path_accuracy"] >= thresholds["join_path_accuracy_min"]
        and metrics["ambiguous_path_detection"] is True
        and metrics["unauthorized_metadata_leaks"] <= thresholds["unauthorized_metadata_leaks_max"]
        and metrics["p95_ms"] <= thresholds["p95_ms_max"]
        and metrics["mean_schema_reduction_ratio"] >= thresholds["mean_schema_reduction_ratio_min"]
    )
    return {
        "format": FORMAT,
        "version": VERSION,
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "fixture": {
            "table_count": len(schema),
            "column_count": sum(len(columns) for columns in schema.values()),
            "synthetic": True,
            "external_validation": "not_run",
        },
        "metrics": metrics,
        "gate": {"passed": passed, "thresholds": thresholds},
        "cases": case_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.iterations, args.dataset)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
