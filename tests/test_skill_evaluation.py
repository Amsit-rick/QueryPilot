from app.domain.skill import SkillDefinition
from app.skill_evaluation import compare_skill_catalogs


def _skill(version: str, triggers: tuple[str, ...]) -> dict:
    return SkillDefinition(
        id="sales-analysis",
        version=version,
        name="销售分析",
        description="销售问题路由测试",
        intents=("sales_query",),
        trigger_terms=triggers,
        allowed_tools=("schema.list", "query.plan"),
        workflow_steps=("确认指标", "规划只读查询"),
    ).model_dump(mode="json")


def test_skill_evaluation_compares_routes_and_reports_expected_match_rate():
    cases = [
        {"id": "sales", "question": "查询销售额", "expected_skill_id": "sales-analysis"},
        {"id": "gross-margin", "question": "查询毛利", "expected_skill_id": "sales-analysis"},
        {"id": "unrelated", "question": "查天气", "expected_skill_id": None},
    ]

    report = compare_skill_catalogs(
        baseline=[_skill("1.0.0", ("销售",))],
        candidate=[_skill("1.1.0", ("销售", "毛利"))],
        cases=cases,
    )

    assert report["summary"] == {
        "cases": 3,
        "route_changes": 1,
        "version_changes": 2,
        "baseline_matches": 2,
        "candidate_matches": 3,
        "baseline_match_rate": 2 / 3,
        "candidate_match_rate": 1.0,
    }
    changed = next(item for item in report["cases"] if item["id"] == "gross-margin")
    assert changed["baseline"]["skill_id"] is None
    assert changed["candidate"]["skill_id"] == "sales-analysis"
    assert changed["candidate"]["matched_terms"] == ["毛利"]
    assert changed["route_changed"] is True
    assert changed["version_changed"] is True


def test_skill_evaluation_rejects_duplicate_question_ids():
    case = {"id": "same", "question": "查询销售额", "expected_skill_id": "sales-analysis"}
    try:
        compare_skill_catalogs(baseline=[], candidate=[], cases=[case, case])
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate case IDs should be rejected")
