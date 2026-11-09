from app.services.context_service import QueryContextService


def test_context_service_loads_catalog_before_retrieval():
    calls = []
    service = QueryContextService(
        load_catalog=lambda path: calls.append(("load", path)) or {"version": "v1"},
        retrieve=lambda question, catalog, limit: calls.append(("retrieve", question, catalog, limit)) or {"catalog_version": catalog["version"]},
        catalog_path="catalog.json",
    )
    assert service.retrieve("sales", 2) == {"catalog_version": "v1"}
    assert calls == [("load", "catalog.json"), ("retrieve", "sales", {"version": "v1"}, 2)]


def test_context_service_merges_semantics_values_and_controlled_expansion():
    service = QueryContextService(
        load_catalog=lambda _path: {"version": "v2"},
        retrieve=lambda _question, catalog, _limit: {"catalog_version": catalog["version"], "examples": [], "metrics": []},
        catalog_path="catalog.json",
        semantic_search=lambda _question, _limit: [{
            "id": "metric-1",
            "logical_id": "metric",
            "kind": "metric",
            "name": "收入",
            "definition": {"aliases": ["营收", "销售收入"], "tables": ["sales"], "columns": ["amount"]},
            "version": 1,
        }],
        value_search=lambda _question, _limit: [{
            "id": "region-values-1",
            "logical_id": "region-values",
            "version": 1,
            "name": "区域值词典",
            "table": "sales",
            "column": "region",
            "value": "EAST",
            "matched_text": "华东",
            "score": 100,
        }],
    )

    result = service.retrieve("统计华东营收", 3)

    assert result["catalog_version"] == "v2"
    assert result["semantics"][0]["name"] == "收入"
    assert result["field_values"][0]["value"] == "EAST"
    assert result["query_expansion"] == {
        "terms": ["收入", "营收", "销售收入", "EAST", "华东"],
        "evidence_count": 2,
        "mode": "curated",
    }
