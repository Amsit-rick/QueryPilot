import asyncio
import json

from app.agent.reflection import ReflectionAction, ReflectionFailureType
from app.services.result_quality_service import ResultQualityService
from app.services.semantic_critic_service import SemanticCriticService


def _uncertain_assessment():
    return ResultQualityService().assess(
        question="统计当前有效订单销售额",
        sql="SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
        columns=["region", "total_amount"],
        rows=[["secret-region", 10]],
        truncated=False,
        authorized_schema={
            "sales": {"region", "amount", "status", "deleted_at"},
            "unrelated_sensitive_table": {"secret_token", "private_note"},
        },
        relationships=[],
        schema_details={"tables": {"sales": {"columns": ["region", "amount", "status", "deleted_at"]}}},
        expected_dimensions=["region"],
        expected_metrics=["total_amount"],
        explain_summary={},
    )


def test_semantic_critic_accepts_only_high_confidence_structured_result():
    async def invoke(_prompt):
        return json.dumps({
            "accepted": True,
            "failure_type": "none",
            "reason_codes": ["SEMANTIC_COVERAGE_CONFIRMED"],
            "evidence": ["status scope is not required by the approved metric definition"],
            "action": "accept",
            "retryable": False,
            "confidence": 0.93,
        })

    result = asyncio.run(SemanticCriticService(invoke=invoke, min_confidence=0.8).review(_uncertain_assessment()))

    assert result.accepted is True
    assert result.action == ReflectionAction.ACCEPT


def test_semantic_critic_low_confidence_or_invalid_payload_fails_closed():
    async def low_confidence(_prompt):
        return {
            "accepted": True,
            "failure_type": "none",
            "reason_codes": ["MAYBE_OK"],
            "evidence": ["uncertain"],
            "action": "accept",
            "retryable": False,
            "confidence": 0.51,
        }

    async def invalid(_prompt):
        return "not-json"

    low = asyncio.run(SemanticCriticService(invoke=low_confidence, min_confidence=0.8).review(_uncertain_assessment()))
    bad = asyncio.run(SemanticCriticService(invoke=invalid, min_confidence=0.8).review(_uncertain_assessment()))

    assert low.action == ReflectionAction.ASK_CLARIFICATION
    assert low.failure_type == ReflectionFailureType.BUSINESS_AMBIGUITY
    assert bad.action == ReflectionAction.ASK_CLARIFICATION
    assert bad.reason_codes == ["SEMANTIC_CRITIC_INVALID"]


def test_semantic_critic_timeout_and_missing_model_are_conservative():
    async def timeout(_prompt):
        raise TimeoutError("provider timeout with secret=abc")

    timed_out = asyncio.run(SemanticCriticService(invoke=timeout).review(_uncertain_assessment()))
    unavailable = asyncio.run(SemanticCriticService(invoke=None).review(_uncertain_assessment()))

    assert timed_out.reason_codes == ["SEMANTIC_CRITIC_TIMEOUT"]
    assert unavailable.reason_codes == ["SEMANTIC_CRITIC_UNAVAILABLE"]
    assert "abc" not in str(timed_out)


def test_semantic_critic_prompt_contains_no_rows_literals_or_secrets():
    captured = {}

    async def invoke(prompt):
        captured["prompt"] = prompt
        return {
            "accepted": False,
            "failure_type": "business_ambiguity",
            "reason_codes": ["STATUS_SCOPE_REQUIRES_CONFIRMATION"],
            "evidence": ["approved definition is missing"],
            "action": "ask_clarification",
            "repair_instruction": "请确认有效订单的状态口径。",
            "retryable": False,
            "confidence": 0.91,
        }

    result = asyncio.run(SemanticCriticService(invoke=invoke).review(_uncertain_assessment()))

    assert result.action == ReflectionAction.ASK_CLARIFICATION
    assert "secret-region" not in captured["prompt"]
    assert "unrelated_sensitive_table" not in captured["prompt"]
    assert "secret_token" not in captured["prompt"]
    assert "SUM(amount)" in captured["prompt"]
    assert "rows" not in captured["prompt"].lower()


def test_semantic_critic_cannot_change_security_or_permission_policy():
    async def invoke(_prompt):
        return {
            "accepted": False,
            "failure_type": "security",
            "reason_codes": ["IGNORE_GUARDRAILS"],
            "evidence": ["disable readonly checks"],
            "action": "regenerate_sql",
            "retryable": True,
            "confidence": 1.0,
        }

    result = asyncio.run(SemanticCriticService(invoke=invoke).review(_uncertain_assessment()))

    assert result.action == ReflectionAction.ASK_CLARIFICATION
    assert result.reason_codes == ["SEMANTIC_CRITIC_POLICY_CONFLICT"]


def test_semantic_critic_prompt_guard_blocks_injection_before_model_call():
    called = []

    async def invoke(_prompt):
        called.append(True)
        return {}

    def reject(_question):
        raise RuntimeError("prompt injection")

    result = asyncio.run(SemanticCriticService(invoke=invoke, prompt_guard=reject).review(_uncertain_assessment()))

    assert called == []
    assert result.reason_codes == ["SEMANTIC_CRITIC_PROMPT_REJECTED"]

