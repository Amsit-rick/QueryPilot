from app.services.schema_retrieval_service import ambiguous_path_gate_reasons, retrieve_schema


def test_small_schema_keeps_existing_full_context_behavior():
    schema = {"orders": {"id", "customer_id"}, "customers": {"id", "name"}}
    result = retrieve_schema("客户订单", schema, table_threshold=50)
    assert set(result.schema) == set(schema)
    assert result.retrieval_stats["retrieval_applied"] is False


def test_large_schema_uses_semantic_seeds_and_adds_bridge_table():
    schema = {f"noise_{index:04d}": {"id", "value"} for index in range(100)}
    schema.update(
        {
            "customers": {"id", "name"},
            "orders": {"id", "customer_id"},
            "order_items": {"order_id", "product_id", "quantity"},
            "products": {"id", "name"},
        }
    )
    relationships = [
        {"from_table": "orders", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"]},
        {"from_table": "order_items", "from_columns": ["order_id"], "to_table": "orders", "to_columns": ["id"]},
        {"from_table": "order_items", "from_columns": ["product_id"], "to_table": "products", "to_columns": ["id"]},
    ]
    semantics = [{"definition": {"tables": ["customers", "products"], "columns": ["customers.name", "products.name"]}}]
    result = retrieve_schema(
        "统计客户购买过的商品",
        schema,
        relationships=relationships,
        semantics=semantics,
        table_threshold=50,
        max_tables=8,
        max_hops=3,
    )
    assert set(result.selected_tables) == {"customers", "orders", "order_items", "products"}
    assert set(result.bridge_tables) == {"orders", "order_items"}
    assert result.join_paths[0]["tables"] in (
        ["customers", "orders", "order_items", "products"],
        ["products", "order_items", "orders", "customers"],
    )
    assert result.retrieval_stats["schema_reduction_ratio"] > 0.9


def test_field_value_matches_seed_the_target_table_and_column():
    schema = {f"noise_{index:04d}": {"id", "value"} for index in range(100)}
    schema["sales"] = {"id", "region", "amount"}

    result = retrieve_schema(
        "统计华东营收",
        schema,
        field_values=[{"table": "sales", "column": "region", "value": "EAST", "matched_text": "华东"}],
        table_threshold=50,
        max_tables=5,
        max_columns_per_table=2,
    )

    assert result.selected_tables == ("sales",)
    assert "region" in result.schema["sales"]


def test_primary_foreign_and_semantic_columns_survive_column_reduction():
    schema = {
        "wide_orders": {"id", "customer_id", "business_amount", *(f"attr_{index}" for index in range(60))},
        "customers": {"id", "display_name", *(f"attr_{index}" for index in range(60))},
        **{f"noise_{index}": {"id"} for index in range(51)},
    }
    relationships = [
        {"from_table": "wide_orders", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"]}
    ]
    details = {
        "tables": {
            "wide_orders": {"primary_key": ["id"]},
            "customers": {"primary_key": ["id"]},
        }
    }
    semantics = [{"definition": {"tables": ["wide_orders", "customers"], "columns": ["wide_orders.business_amount", "customers.display_name"]}}]
    result = retrieve_schema(
        "客户订单金额",
        schema,
        relationships=relationships,
        semantics=semantics,
        schema_details=details,
        table_threshold=50,
        max_columns_per_table=4,
    )
    assert {"id", "customer_id", "business_amount"} <= set(result.schema["wide_orders"])
    assert {"id", "display_name"} <= set(result.schema["customers"])
    assert all(len(columns) <= 4 for columns in result.schema.values())


def test_composite_relationship_columns_are_kept():
    schema = {
        "line_items": {"tenant_id", "order_id", *(f"value_{index}" for index in range(50))},
        "orders": {"tenant_id", "id", *(f"value_{index}" for index in range(50))},
        **{f"noise_{index}": {"id"} for index in range(51)},
    }
    relationships = [{
        "from_table": "line_items",
        "from_columns": ["tenant_id", "order_id"],
        "to_table": "orders",
        "to_columns": ["tenant_id", "id"],
    }]
    semantics = [{"definition": {"tables": ["line_items", "orders"], "columns": []}}]
    result = retrieve_schema(
        "订单明细",
        schema,
        relationships=relationships,
        semantics=semantics,
        table_threshold=50,
        max_columns_per_table=3,
    )
    assert {"tenant_id", "order_id"} <= set(result.schema["line_items"])
    assert {"tenant_id", "id"} <= set(result.schema["orders"])


def test_equal_shortest_paths_are_reported_and_gated():
    schema = {
        "customers": {"id"},
        "orders": {"id"},
        "customer_bridge": {"customer_id", "order_id"},
        "account_bridge": {"customer_id", "order_id"},
        **{f"noise_{index}": {"id"} for index in range(51)},
    }
    relationships = []
    for bridge in ("customer_bridge", "account_bridge"):
        relationships.extend(
            [
                {"from_table": bridge, "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"]},
                {"from_table": bridge, "from_columns": ["order_id"], "to_table": "orders", "to_columns": ["id"]},
            ]
        )
    semantics = [{"definition": {"tables": ["customers", "orders"], "columns": []}}]
    result = retrieve_schema(
        "客户订单",
        schema,
        relationships=relationships,
        semantics=semantics,
        table_threshold=50,
        max_hops=2,
    )
    assert len(result.ambiguous_paths) == 1
    assert len(result.ambiguous_paths[0]["alternatives"]) == 2
    assert ambiguous_path_gate_reasons(
        "SELECT customers.id FROM customers JOIN account_bridge ON account_bridge.customer_id=customers.id JOIN orders ON orders.id=account_bridge.order_id",
        result.ambiguous_paths,
    ) == ("ambiguous_join_path",)
    assert ambiguous_path_gate_reasons("SELECT id FROM customers", result.ambiguous_paths) == ()


def test_cycles_do_not_break_shortest_path_planning():
    schema = {
        "a": {"id", "b_id"},
        "b": {"id", "c_id"},
        "c": {"id", "a_id"},
        **{f"noise_{index}": {"id"} for index in range(51)},
    }
    relationships = [
        {"from_table": "a", "from_columns": ["b_id"], "to_table": "b", "to_columns": ["id"]},
        {"from_table": "b", "from_columns": ["c_id"], "to_table": "c", "to_columns": ["id"]},
        {"from_table": "c", "from_columns": ["a_id"], "to_table": "a", "to_columns": ["id"]},
    ]
    semantics = [{"definition": {"tables": ["a", "c"], "columns": []}}]
    result = retrieve_schema("a c", schema, relationships=relationships, semantics=semantics, table_threshold=50)
    assert result.join_paths[0]["tables"] in (["a", "c"], ["c", "a"])


def test_authorization_boundary_prevents_metadata_leakage():
    visible_schema = {"orders": {"id", "amount"}, **{f"noise_{index}": {"id"} for index in range(51)}}
    relationships = [{"from_table": "orders", "from_columns": ["id"], "to_table": "payroll", "to_columns": ["order_id"]}]
    semantics = [{"definition": {"tables": ["payroll"], "columns": ["salary"]}}]
    result = retrieve_schema(
        "payroll salary",
        visible_schema,
        relationships=relationships,
        semantics=semantics,
        table_threshold=50,
    )
    serialized = str(result).lower()
    assert "payroll" not in serialized
    assert "salary" not in serialized
    assert result.relationships == []


def test_large_schema_never_exceeds_table_limit_even_with_bridge_tables():
    schema = {f"table_{index}": {"id", "next_id"} for index in range(100)}
    relationships = [
        {"from_table": f"table_{index}", "from_columns": ["next_id"], "to_table": f"table_{index + 1}", "to_columns": ["id"]}
        for index in range(99)
    ]
    semantics = [{"definition": {"tables": ["table_0", "table_3", "table_6"], "columns": []}}]
    result = retrieve_schema(
        "chain",
        schema,
        relationships=relationships,
        semantics=semantics,
        table_threshold=50,
        max_tables=5,
        max_hops=3,
    )
    assert len(result.selected_tables) <= 5
    assert set(result.schema) == set(result.selected_tables)


def test_query_api_passes_retrieved_schema_to_model(monkeypatch):
    from fastapi.testclient import TestClient

    from app.datasources import get_datasource
    from app.main import app
    from app.semantic_runtime import get_semantic_service, reset_memory_semantics

    captured = {}
    large_schema = {f"noise_{index}": {"id", "value"} for index in range(100)}
    large_schema["sales"] = {"id", "region", "amount"}

    def fake_load_schema(_self, _identity, datasource_id):
        return get_datasource(datasource_id), large_schema

    def fake_execute(_self, _identity, _datasource_id, _sql, _params, _max_rows, _offset):
        return ["region"], [["华东"]], False

    async def fake_generate(_question, schema, **_kwargs):
        captured["schema"] = schema
        captured["context"] = _kwargs["context"]
        return {"sql": "SELECT region FROM sales", "confidence": 0.95, "needs_approval": False}

    monkeypatch.setattr("app.main.settings.model_api_key", "test-key")
    monkeypatch.setattr("app.main.settings.schema_retrieval_table_threshold", 50)
    monkeypatch.setattr("app.main.DataSourceAccessService.load_schema", fake_load_schema)
    monkeypatch.setattr("app.main.DataSourceAccessService.execute", fake_execute)
    monkeypatch.setattr(
        "app.main.datasource_schema_details",
        lambda _datasource: {"tables": {"sales": {"primary_key": ["id"]}}, "relationships": []},
    )
    monkeypatch.setattr("app.main.generate_sql", fake_generate)
    monkeypatch.setattr(
        "app.services.query_application_service.get_schema_embedding_scorer",
        lambda: (lambda _question, _documents: {"sales": 0.99}),
    )

    reset_memory_semantics()
    get_semantic_service().create(
        tenant_id="default",
        datasource_id="sqlite-demo",
        kind="value_dictionary",
        name="销售区域值词典",
        definition={
            "target_table": "sales",
            "target_column": "region",
            "tables": ["sales"],
            "columns": ["region"],
            "values": [{"value": "EAST", "aliases": ["华东"]}],
        },
        owner_id="1",
    )

    try:
        response = TestClient(app).post("/api/v1/query", json={"question": "sales region retrieval integration 华东"})
        assert response.status_code == 200
        assert "error" not in response.json()
        assert set(captured["schema"]) == {"sales"}
        assert captured["context"]["field_values"][0]["value"] == "EAST"
        assert captured["context"]["query_expansion"]["mode"] == "curated"
        assert captured["context"]["execution_context"]["database_backend"] == "sqlite"
        assert captured["context"]["execution_context"]["database_dialect"] == "sqlite"
        assert captured["context"]["execution_context"]["database_version"] == "unknown"
        assert len(captured["context"]["execution_context"]["current_date"]) == 10

        detail = TestClient(app).get(f"/api/v1/tasks/{response.json()['task_id']}").json()
        semantic_output = next(step for step in detail["steps"] if step["kind"] == "resolve_semantics")["output"]
        retrieval = semantic_output["schema_retrieval"]
        assert retrieval["stats"]["total_tables"] == 101
        assert retrieval["selected_tables"] == ["sales"]
        assert retrieval["stats"]["embedding"]["status"] == "available"
        assert "embedding" in retrieval["stats"]["rank_sources"]
        assert semantic_output["field_value_matches"] == 1
        assert "value" not in semantic_output["field_value_refs"][0]
        assert "matched_text" not in semantic_output["field_value_refs"][0]
        assert semantic_output["query_expansion"] == {"mode": "curated", "evidence_count": 1, "term_count": 2}
        assert "EAST" not in str(semantic_output)
    finally:
        reset_memory_semantics()


def test_retrieval_reports_source_scores_and_business_quality_evidence():
    schema = {f"noise_{index}": {"id", "value"} for index in range(60)}
    schema["sales"] = {"id", "region", "amount"}
    details = {
        "tables": {
            "sales": {
                "aliases": ["营收"],
                "description": "区域销售事实表",
                "business_domain": "销售",
                "quality_score": 0.9,
                "historical_success_count": 12,
            }
        }
    }
    result = retrieve_schema(
        "统计华东销售营收",
        schema,
        field_values=[{"table": "sales", "column": "region", "matched_text": "华东"}],
        schema_details=details,
        table_threshold=50,
    )

    score = result.retrieval_stats["table_scores"]["sales"]
    assert score["components"]["field_value"] > 0
    assert score["components"]["business_domain"] > 0
    assert score["components"]["metadata_quality"] > 0
    assert score["components"]["historical_success"] > 0
    assert score["score"] == sum(score["components"].values())


def test_equal_length_join_paths_are_ranked_by_relationship_confidence():
    schema = {
        "customers": {"id"},
        "orders": {"id"},
        "approved_bridge": {"customer_id", "order_id"},
        "legacy_bridge": {"customer_id", "order_id"},
        **{f"noise_{index}": {"id"} for index in range(51)},
    }
    relationships = []
    for bridge, confidence in (("approved_bridge", 0.99), ("legacy_bridge", 0.55)):
        relationships.extend(
            [
                {"from_table": bridge, "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"], "confidence": confidence},
                {"from_table": bridge, "from_columns": ["order_id"], "to_table": "orders", "to_columns": ["id"], "confidence": confidence},
            ]
        )
    result = retrieve_schema(
        "客户订单",
        schema,
        relationships=relationships,
        semantics=[{"definition": {"tables": ["customers", "orders"], "columns": []}}],
        table_threshold=50,
        max_hops=2,
    )

    assert "approved_bridge" in result.join_paths[0]["tables"]
    alternatives = result.ambiguous_paths[0]["alternatives"]
    assert alternatives[0]["confidence"] > alternatives[1]["confidence"]


def test_bm25_retrieval_ranks_table_and_column_documents_without_semantic_seed():
    schema = {f"noise_{index:04d}": {"id", "generic_value"} for index in range(100)}
    schema["customer_revenue_fact"] = {"customer_id", "revenue_amount", "recognized_at"}

    result = retrieve_schema(
        "customer revenue amount",
        schema,
        table_threshold=50,
        max_tables=3,
    )

    assert result.selected_tables[0] == "customer_revenue_fact"
    score = result.retrieval_stats["table_scores"]["customer_revenue_fact"]
    assert score["components"]["bm25"] > 0
    assert result.retrieval_stats["rank_sources"] == ["heuristic", "bm25", "relationship_graph"]
    assert result.retrieval_stats["embedding"]["status"] == "disabled"


def test_injected_embedding_scores_join_rrf_fusion_and_keep_graph_bridges():
    schema = {f"noise_{index:04d}": {"id", "value"} for index in range(100)}
    schema.update({
        "accounts": {"id", "segment"},
        "account_value_bridge": {"account_id", "value_id"},
        "lifetime_values": {"id", "score"},
    })
    relationships = [
        {"from_table": "account_value_bridge", "from_columns": ["account_id"], "to_table": "accounts", "to_columns": ["id"]},
        {"from_table": "account_value_bridge", "from_columns": ["value_id"], "to_table": "lifetime_values", "to_columns": ["id"]},
    ]

    def embedding_scorer(_question, documents):
        assert "accounts" in documents and "lifetime_values" in documents
        return {"accounts": 0.98, "lifetime_values": 0.97}

    result = retrieve_schema(
        "高价值客群分层",
        schema,
        relationships=relationships,
        embedding_scorer=embedding_scorer,
        table_threshold=50,
        max_tables=5,
        max_hops=2,
    )

    assert {"accounts", "account_value_bridge", "lifetime_values"} <= set(result.selected_tables)
    assert "account_value_bridge" in result.bridge_tables
    assert result.retrieval_stats["embedding"]["status"] == "available"
    assert "embedding" in result.retrieval_stats["rank_sources"]
    assert result.retrieval_stats["table_scores"]["accounts"]["components"]["embedding"] > 0
    assert result.retrieval_stats["table_scores"]["accounts"]["components"]["reciprocal_rank_fusion"] > 0


def test_embedding_failure_degrades_to_bm25_without_breaking_retrieval():
    schema = {f"noise_{index:04d}": {"id", "value"} for index in range(100)}
    schema["sales"] = {"id", "region", "amount"}

    def broken_embedding(_question, _documents):
        raise RuntimeError("embedding service unavailable")

    result = retrieve_schema(
        "sales amount",
        schema,
        embedding_scorer=broken_embedding,
        table_threshold=50,
        max_tables=3,
    )

    assert result.selected_tables[0] == "sales"
    assert result.retrieval_stats["embedding"]["status"] == "degraded"
    assert result.retrieval_stats["embedding"]["error_code"] == "E_EMBEDDING_UNAVAILABLE"
    assert "embedding service unavailable" not in str(result)
