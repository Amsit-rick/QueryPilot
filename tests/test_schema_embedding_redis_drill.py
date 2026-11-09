from scripts.schema_embedding_redis_drill import REQUIRED_CHECKS, evaluate_checks


def test_embedding_redis_drill_requires_every_safety_check():
    checks = {name: True for name in REQUIRED_CHECKS}
    assert evaluate_checks(checks) == {"passed": True, "failed_checks": []}

    checks["raw_schema_absent"] = False
    result = evaluate_checks(checks)
    assert result["passed"] is False
    assert result["failed_checks"] == ["raw_schema_absent"]
