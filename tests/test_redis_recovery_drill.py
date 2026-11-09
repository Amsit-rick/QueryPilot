from scripts.redis_recovery_drill import evaluate_drill


def test_redis_recovery_drill_requires_restart_checkpoint_and_fencing_evidence():
    checks = {
        "redis_started": True,
        "live_lease_exclusion": True,
        "outage_observed": True,
        "redis_restarted": True,
        "checkpoint_restored": True,
        "fencing_token_monotonic": True,
        "stale_holder_rejected": True,
        "new_holder_committed": True,
    }

    assert evaluate_drill(checks)["passed"] is True
    checks["stale_holder_rejected"] = False
    failed = evaluate_drill(checks)
    assert failed["passed"] is False
    assert failed["failed_checks"] == ["stale_holder_rejected"]
