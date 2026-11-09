import pytest

from app.domain.skill import SkillDefinition
from app.repositories.memory_skill_repository import InMemorySkillRepository
from app.services.skill_service import SkillService, get_skill_service


def _definition(version: str, *, description: str = "新版销售分析") -> SkillDefinition:
    return SkillDefinition(
        id="sales-analysis",
        version=version,
        name="销售分析",
        description=description,
        intents=("sales_query",),
        trigger_terms=("销售", "营收"),
        allowed_tools=("schema.list", "query.plan", "result.get"),
        workflow_steps=("确认口径", "生成只读计划"),
        priority=70,
    )


def test_builtin_skill_catalog_is_versioned_and_read_only():
    skills = get_skill_service().list_skills()

    assert {skill["id"] for skill in skills} == {
        "schema-exploration",
        "sales-analysis",
        "receivables-analysis",
        "trend-analysis",
        "sql-diagnostics",
    }
    assert all(skill["version"] == "1.0.0" for skill in skills)
    assert all(skill["read_only"] is True for skill in skills)
    assert all("query.execute" not in skill["allowed_tools"] for skill in skills)


def test_skill_router_prefers_specific_analysis_and_returns_evidence():
    service = get_skill_service()

    trend = service.route("对比各区域销售额的同比趋势")
    receivables = service.route("统计各区域的待回款金额")

    assert trend["selected_skill"]["id"] == "trend-analysis"
    assert {"同比", "趋势"}.issubset(set(trend["evidence"]["matched_terms"]))
    assert receivables["selected_skill"]["id"] == "receivables-analysis"
    assert "待回款" in receivables["evidence"]["matched_terms"]


def test_skill_router_does_not_force_an_unmatched_workflow():
    result = get_skill_service().route("帮我处理一下")

    assert result["selected_skill"] is None
    assert result["requires_clarification"] is True
    assert result["candidates"] == []


def test_skill_router_is_deterministic_and_does_not_return_prompt_secrets():
    service = get_skill_service()
    first = service.route("检查 SQL 为什么执行慢")
    second = service.route("检查 SQL 为什么执行慢")

    assert first == second
    assert first["selected_skill"]["id"] == "sql-diagnostics"
    assert "system_prompt" not in first["selected_skill"]
    assert "instructions" not in first["selected_skill"]


def test_draft_version_does_not_change_runtime_catalog():
    service = SkillService(repository=InMemorySkillRepository())
    identity = {"tenant_id": "tenant-a", "subject": "user-1", "roles": ["viewer"]}

    draft = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.1.0"),
    )

    active = next(item for item in service.list_skills(identity) if item["id"] == "sales-analysis")
    assert draft.state == "draft"
    assert active["version"] == "1.0.0"


def test_canary_is_stable_tenant_scoped_and_falls_back_to_active():
    service = SkillService(repository=InMemorySkillRepository())
    draft = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.1.0"),
    )
    service.start_canary(draft.id, tenant_id="tenant-a", actor_id="admin-1", percentage=37)

    first = service.preview_resolution(
        tenant_id="tenant-a", subject="viewer-17", skill_id="sales-analysis"
    )
    second = service.preview_resolution(
        tenant_id="tenant-a", subject="viewer-17", skill_id="sales-analysis"
    )
    other_tenant = service.preview_resolution(
        tenant_id="tenant-b", subject="viewer-17", skill_id="sales-analysis"
    )

    assert first == second
    assert 0 <= first["bucket"] < 100
    assert first["selected_version"] in {"1.0.0", "1.1.0"}
    assert first["canary_version"] == "1.1.0"
    assert other_tenant["selected_version"] == "1.0.0"
    assert other_tenant["canary_version"] is None


def test_query_route_exposes_the_selected_release_and_declarative_behavior():
    service = SkillService(repository=InMemorySkillRepository())
    identity = {"tenant_id": "tenant-a", "subject": "viewer-17", "roles": ["viewer"]}
    draft = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.1.0", description="按业务口径统计销售"),
    )
    service.publish_version(draft.id, tenant_id="tenant-a", actor_id="admin-1")

    routed = service.route("查询营收", identity=identity, datasource_id="sales-db")

    assert routed["selected_skill"]["id"] == "sales-analysis"
    assert routed["selected_skill"]["version"] == "1.1.0"
    assert routed["selected_skill"]["deployment"]["state"] == "active"
    assert routed["selected_skill"]["workflow_steps"] == ["确认口径", "生成只读计划"]
    assert routed["evidence"]["matched_terms"] == ["营收"]


def test_route_without_match_has_no_runtime_skill_context():
    routed = get_skill_service().route("帮我处理一下")

    assert routed["selected_skill"] is None
    assert routed["evidence"]["matched_terms"] == []


def test_skill_declarations_have_server_side_item_length_limits():
    with pytest.raises(ValueError):
        SkillDefinition(
            id="sales-analysis",
            version="1.1.0",
            name="销售分析",
            description="新版销售分析",
            intents=("sales_query",),
            trigger_terms=("销售",),
            allowed_tools=("schema.list",),
            workflow_steps=("超长" * 100,),
        )


def test_publish_and_rollback_keep_one_active_version_and_history():
    repository = InMemorySkillRepository()
    service = SkillService(repository=repository)
    first = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.1.0", description="第一版"),
    )
    second = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.2.0", description="第二版"),
    )

    service.publish_version(first.id, tenant_id="tenant-a", actor_id="admin-1")
    service.publish_version(second.id, tenant_id="tenant-a", actor_id="admin-1")
    versions = service.list_versions("tenant-a", "sales-analysis")
    assert [item.state for item in versions].count("active") == 1
    assert next(item for item in versions if item.id == first.id).state == "retired"
    assert service.preview_resolution(
        tenant_id="tenant-a", subject="viewer-1", skill_id="sales-analysis"
    )["selected_version"] == "1.2.0"

    restored = service.rollback_version(
        first.id, tenant_id="tenant-a", actor_id="admin-2"
    )
    versions = service.list_versions("tenant-a", "sales-analysis")
    assert restored.state == "active"
    assert len(versions) == 3
    assert next(item for item in versions if item.id == second.id).state == "retired"
    assert service.preview_resolution(
        tenant_id="tenant-a", subject="viewer-1", skill_id="sales-analysis"
    )["selected_version"] == "1.1.0"


def test_first_publish_snapshots_builtin_baseline_for_real_rollback():
    service = SkillService(repository=InMemorySkillRepository())
    draft = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.1.0"),
    )
    service.publish_version(draft.id, tenant_id="tenant-a", actor_id="admin-1")
    baseline = next(
        item
        for item in service.list_versions("tenant-a", "sales-analysis")
        if item.definition.version == "1.0.0"
    )

    restored = service.rollback_version(
        baseline.id, tenant_id="tenant-a", actor_id="admin-2"
    )

    assert baseline.state == "retired"
    assert baseline.created_by == "system:builtin"
    assert restored.state == "active"
    assert service.preview_resolution(
        tenant_id="tenant-a", subject="viewer-1", skill_id="sales-analysis"
    )["selected_version"] == "1.0.0"


def test_skill_versions_reject_cross_tenant_management_and_duplicate_semver():
    service = SkillService(repository=InMemorySkillRepository())
    created = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        definition=_definition("1.1.0"),
    )

    with pytest.raises(ValueError, match="版本已存在"):
        service.create_version(
            tenant_id="tenant-a",
            actor_id="admin-1",
            definition=_definition("1.1.0"),
        )
    with pytest.raises(LookupError, match="不存在"):
        service.publish_version(created.id, tenant_id="tenant-b", actor_id="admin-2")
