from scripts.multiprocess_metrics_acceptance import REQUIRED_CHECKS, evaluate_checks


def test_multiprocess_metrics_acceptance_requires_all_checks():
    checks = {name: True for name in REQUIRED_CHECKS}
    assert evaluate_checks(checks) == {"passed": True, "failed_checks": []}

    checks["multiple_worker_metric_files"] = False
    result = evaluate_checks(checks)
    assert result["passed"] is False
    assert result["failed_checks"] == ["multiple_worker_metric_files"]
