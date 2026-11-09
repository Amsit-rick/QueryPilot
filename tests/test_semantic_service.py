import pytest

from app.domain.semantic import SemanticEntry
from app.repositories.memory_semantic_repository import InMemorySemanticRepository
from app.services.semantic_service import SemanticService


def test_semantic_entry_updates_create_traceable_versions():
    service = SemanticService(InMemorySemanticRepository())
    created = service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="销售额", definition={"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"]}, owner_id="1")
    updated = service.update(created.id, tenant_id="t1", changes={"definition": {"formula": "SUM(net_amount)", "tables": ["sales"], "columns": ["net_amount"]}}, owner_id="2")

    assert created.version == 1
    assert updated.version == 2
    assert updated.previous_id == created.id
    assert service.history(created.id, "t1") == [updated, created]


def test_semantic_search_filters_tenant_datasource_and_forbidden_fields():
    service = SemanticService(InMemorySemanticRepository())
    service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="销售额", definition={"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"]}, owner_id="1")
    service.create(tenant_id="t1", datasource_id="demo", kind="alias", name="客户", definition={"target_table": "customers", "target_column": "name", "tables": ["customers"], "columns": ["name"]}, owner_id="1")
    service.create(tenant_id="t2", datasource_id="demo", kind="metric", name="秘密指标", definition={"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"]}, owner_id="1")

    visible = service.search(question="销售额 客户", tenant_id="t1", datasource_id="demo", allowed_schema={"sales": ["region", "amount"]})

    assert [item.name for item in visible] == ["销售额"]


def test_semantic_service_rejects_unknown_kind_and_missing_references():
    service = SemanticService(InMemorySemanticRepository())
    with pytest.raises(ValueError, match="E_SEMANTIC_KIND"):
        service.create(tenant_id="t1", datasource_id="demo", kind="tool", name="执行器", definition={}, owner_id="1")
    with pytest.raises(ValueError, match="E_SEMANTIC_DEFINITION"):
        service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="销售额", definition={"formula": "SUM(amount)"}, owner_id="1")
    with pytest.raises(ValueError, match="未知字段"):
        service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="销售额", definition={"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"], "secret": "do not inject"}, owner_id="1")


def test_semantic_update_rejects_historical_version_and_search_ignores_unrelated_entries():
    service = SemanticService(InMemorySemanticRepository())
    first = service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="销售额", definition={"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"]}, owner_id="1")
    service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="订单数", definition={"formula": "COUNT(id)", "tables": ["sales"], "columns": ["id"]}, owner_id="1")
    service.update(first.id, tenant_id="t1", changes={"description": "v2"}, owner_id="1")
    with pytest.raises(ValueError, match="E_SEMANTIC_VERSION_CONFLICT"):
        service.update(first.id, tenant_id="t1", changes={"description": "fork"}, owner_id="1")
    results = service.search(question="请查询销售额", tenant_id="t1", datasource_id="demo", allowed_schema={"sales": ["id", "amount"]})
    assert [item.name for item in results] == ["销售额"]


def test_sql_example_requires_readonly_sql_and_keeps_params_as_context_only():
    service = SemanticService(InMemorySemanticRepository())
    created = service.create(
        tenant_id="t1",
        datasource_id="demo",
        kind="sql_example",
        name="按区域统计销售额",
        definition={
            "question": "各区域销售额是多少？",
            "sql": "SELECT region, SUM(amount) AS total FROM sales GROUP BY region",
            "tables": ["sales"],
            "columns": ["region", "amount"],
            "params": {},
        },
        owner_id="1",
    )
    assert created.kind == "sql_example"
    assert service.search("各区域销售额", "t1", "demo", {"sales": ["region", "amount"]}) == [created]

    for sql in ("DELETE FROM sales", "SELECT secret FROM sales"):
        with pytest.raises(ValueError, match="E_SEMANTIC_EXAMPLE_SQL"):
            service.create(
                tenant_id="t1",
                datasource_id="demo",
                kind="sql_example",
                name="非法示例",
                definition={
                    "question": "非法问题",
                    "sql": sql,
                    "tables": ["sales"],
                    "columns": ["amount"],
                },
                owner_id="1",
            )


def test_semantic_search_ranks_aliases_and_returns_match_evidence():
    service = SemanticService(InMemorySemanticRepository())
    service.create(tenant_id="t1", datasource_id="demo", kind="metric", name="收入", definition={"formula": "SUM(amount)", "tables": ["sales"], "columns": ["amount"], "aliases": ["营收", "销售收入"]}, owner_id="1")
    service.create(tenant_id="t1", datasource_id="demo", kind="sql_example", name="区域汇总", definition={"question": "各区域收入", "sql": "SELECT region, SUM(amount) FROM sales GROUP BY region", "tables": ["sales"], "columns": ["region", "amount"], "keywords": ["分区域", "区域销售"]}, owner_id="1")

    matches = service.search_matches("查询营收", "t1", "demo", {"sales": ["region", "amount"]})
    assert matches[0].entry.name == "收入"
    assert matches[0].score >= 90
    assert "aliases" in matches[0].reasons
    assert service.search("查询营收", "t1", "demo", {"sales": ["region", "amount"]})[0].name == "收入"


def test_value_dictionary_recalls_only_matched_authorized_values():
    service = SemanticService(InMemorySemanticRepository())
    created = service.create(
        tenant_id="t1",
        datasource_id="demo",
        kind="value_dictionary",
        name="销售区域值词典",
        definition={
            "target_table": "sales",
            "target_column": "region",
            "tables": ["sales"],
            "columns": ["region"],
            "values": [
                {"value": "EAST", "aliases": ["华东", "东区"]},
                {"value": "WEST", "aliases": ["华西", "西区"]},
            ],
            "keywords": ["区域"],
        },
        owner_id="1",
    )

    matches = service.search_value_matches(
        "统计华东区域营收",
        tenant_id="t1",
        datasource_id="demo",
        allowed_schema={"sales": ["region", "amount"]},
    )

    assert matches == [{
        "id": created.id,
        "logical_id": created.logical_id,
        "version": 1,
        "name": "销售区域值词典",
        "table": "sales",
        "column": "region",
        "value": "EAST",
        "matched_text": "华东",
        "score": 100,
    }]
    assert service.search_value_matches(
        "统计华东区域营收",
        tenant_id="t1",
        datasource_id="demo",
        allowed_schema={"sales": ["amount"]},
    ) == []


def test_value_dictionary_rejects_unbounded_or_malformed_definitions():
    service = SemanticService(InMemorySemanticRepository())

    with pytest.raises(ValueError, match="E_SEMANTIC_VALUES"):
        service.create(
            tenant_id="t1",
            datasource_id="demo",
            kind="value_dictionary",
            name="空词典",
            definition={
                "target_table": "sales",
                "target_column": "region",
                "tables": ["sales"],
                "columns": ["region"],
                "values": [],
            },
            owner_id="1",
        )

    with pytest.raises(ValueError, match="E_SEMANTIC_VALUES"):
        service.create(
            tenant_id="t1",
            datasource_id="demo",
            kind="value_dictionary",
            name="非法词典",
            definition={
                "target_table": "sales",
                "target_column": "region",
                "tables": ["sales"],
                "columns": ["region"],
                "values": [{"value": "EAST", "aliases": ["华东"], "sql": "DROP TABLE sales"}],
            },
            owner_id="1",
        )
