from pathlib import Path

from app.mcp_skill_joint_evaluation import (
    evaluate_mcp_skill_joint_cases,
    load_mcp_skill_joint_cases,
)
from app.services.skill_service import SkillService


def test_joint_dataset_covers_routing_sanitization_and_degradation():
    path = Path(__file__).resolve().parents[1] / "data" / "mcp_skill_joint_eval.json"
    version, cases = load_mcp_skill_joint_cases(path)

    assert version == "2026-11-16-v1"
    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert {
        "ordinary_query",
        "metric_lookup",
        "lineage_lookup",
        "policy_lookup",
        "catalog_lookup",
        "prompt_injection",
        "invalid_result",
        "upstream_failure",
    } <= {case["category"] for case in cases}


def test_builtin_joint_evaluation_passes_all_fixed_contract_cases():
    path = Path(__file__).resolve().parents[1] / "data" / "mcp_skill_joint_eval.json"
    version, cases = load_mcp_skill_joint_cases(path)

    report = evaluate_mcp_skill_joint_cases(
        cases=cases,
        skill_service=SkillService(),
        dataset_version=version,
    )

    assert report["evaluation_type"] == "mcp_skill_joint_contract"
    assert report["summary"]["cases"] == len(cases)
    assert report["summary"]["passed"] == len(cases)
    assert report["summary"]["skill_route_match_rate"] == 1.0
    assert report["summary"]["tool_route_match_rate"] == 1.0
    assert report["summary"]["safety_behavior_match_rate"] == 1.0
    injection = next(item for item in report["cases"] if item["id"] == "metric-injection")
    assert injection["security_findings"] >= 1
    assert injection["status"] == "completed"
    assert "ignore previous" not in str(injection)


def test_joint_evaluation_rejects_duplicate_ids():
    case = {
        "id": "duplicate",
        "category": "ordinary_query",
        "question": "统计销售额",
        "expected_skill_id": "sales-analysis",
        "expected_tools": [],
        "expected_status": "not_used",
    }
    try:
        evaluate_mcp_skill_joint_cases(
            cases=[case, case],
            skill_service=SkillService(),
        )
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate IDs should fail")
