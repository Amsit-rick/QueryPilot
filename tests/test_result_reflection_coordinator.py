import asyncio
import json

from app.agent.contracts import VerificationResult
from app.agent.reflection import ReflectionAction
from app.services.result_reflection_coordinator import ResultReflectionCoordinator
from app.services.result_quality_service import ResultQualityService
from app.services.semantic_critic_service import SemanticCriticService


def _payload():
    return dict(
        question="销售额最高的前 5 个区域",
        sql="SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
        columns=["region", "total_amount"],
        rows=[["east", 10]],
        truncated=False,
        authorized_schema={"sales": {"region", "amount"}},
        relationships=[],
        schema_details={},
        expected_dimensions=["region"],
        expected_metrics=["total_amount"],
        explain_summary={},
    )


def _passing_gate_payload():
    return {
        "format": "query-pilot-reflection-release-gate",
        "version": 1,
        "passed": True,
        "reason_codes": [],
        "inputs": {
            "action": {"dataset_version": "reflection-eval-v1", "passed": True, "sha256": "a" * 64},
            "result": {"dataset_version": "reflection-result-eval-v1", "passed": True, "sha256": "b" * 64},
            "security": {"dataset_version": "reflection-security-eval-v1", "passed": True, "sha256": "c" * 64},
        },
        "metrics": {"final_result_accuracy": 1.0, "security_permission_bypasses": 0, "sensitive_value_leaks": 0, "dangerous_actions": 0},
    }


def test_shadow_mode_records_suggestion_without_changing_query_result(tmp_path):
    coordinator = ResultReflectionCoordinator(
        quality=ResultQualityService(),
        critic=SemanticCriticService(invoke=None),
        requested_mode="shadow",
        gate_report_path=tmp_path / "unused.json",
    )

    result = asyncio.run(coordinator.verify(**_payload()))

    assert isinstance(result, VerificationResult)
    assert result.accepted is True
    assert result.mode == "shadow"
    assert result.suggested_reflection["action"] == "regenerate_sql"


def test_enforced_mode_applies_deterministic_reflection_after_gate(tmp_path):
    report = tmp_path / "gate.json"
    report.write_text(json.dumps(_passing_gate_payload()), encoding="utf-8")
    coordinator = ResultReflectionCoordinator(
        quality=ResultQualityService(),
        critic=SemanticCriticService(invoke=None),
        requested_mode="enforced",
        gate_report_path=report,
    )

    result = asyncio.run(coordinator.verify(**_payload()))

    assert result.accepted is False
    assert result.mode == "enforced"
    assert result.reflection["action"] == "regenerate_sql"


def test_enforced_mode_without_gate_downgrades_to_shadow(tmp_path):
    coordinator = ResultReflectionCoordinator(
        quality=ResultQualityService(),
        critic=SemanticCriticService(invoke=None),
        requested_mode="enforced",
        gate_report_path=tmp_path / "missing.json",
    )

    result = asyncio.run(coordinator.verify(**_payload()))

    assert result.accepted is True
    assert result.mode == "shadow"
    assert result.gate_reason == "offline_gate_missing_or_failed"


def test_explicit_sql_is_never_silently_regenerated(tmp_path):
    report = tmp_path / "gate.json"
    report.write_text(json.dumps(_passing_gate_payload()), encoding="utf-8")
    coordinator = ResultReflectionCoordinator(
        quality=ResultQualityService(),
        critic=SemanticCriticService(invoke=None),
        requested_mode="enforced",
        gate_report_path=report,
    )

    result = asyncio.run(coordinator.verify(**_payload(), provided_sql=True))

    assert result.accepted is False
    assert result.reflection["action"] == ReflectionAction.FAIL.value
    assert result.reflection["reason_codes"] == ["CALLER_SQL_RESULT_REJECTED"]


def test_rules_only_mode_keeps_deterministic_ambiguity_when_critic_disabled(tmp_path):
    coordinator = ResultReflectionCoordinator(
        quality=ResultQualityService(),
        critic=SemanticCriticService(invoke=None),
        critic_enabled=False,
        requested_mode="shadow",
        gate_report_path=tmp_path / "unused.json",
    )
    payload = _payload()
    payload.update({
        "question": "统计当前有效订单销售额",
        "authorized_schema": {"sales": {"region", "amount", "status", "deleted_at"}},
        "schema_details": {"tables": {"sales": {"columns": ["region", "amount", "status", "deleted_at"]}}},
    })

    result = asyncio.run(coordinator.verify(**payload))

    assert result.source == "deterministic"
    assert set(result.reason_codes) == {"STATUS_SCOPE_UNCERTAIN", "SOFT_DELETE_SCOPE_UNCERTAIN"}

