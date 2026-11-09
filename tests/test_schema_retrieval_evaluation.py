from scripts.evaluate_schema_retrieval import evaluate


def test_1500_table_schema_benchmark_has_release_thresholds():
    report = evaluate(iterations=1)

    assert report["format"] == "query-pilot-schema-retrieval-eval"
    assert report["dataset_version"] == "schema-retrieval-eval-v1"
    assert report["fixture"]["table_count"] == 1500
    assert report["gate"]["passed"] is True
    assert report["metrics"]["unauthorized_metadata_leaks"] == 0
    assert report["metrics"]["p95_ms"] <= report["gate"]["thresholds"]["p95_ms_max"]
    assert {"heuristic", "bm25", "fused", "graph_augmented"} <= set(report["metrics"]["source_metrics"])
    assert report["metrics"]["embedding_status"] == "disabled"
    assert isinstance(report["metrics"]["fusion_gain_over_best_source"], float)
    assert report["metrics"]["graph_gain_over_fused"] >= 0
