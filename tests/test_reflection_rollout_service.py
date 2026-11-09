import json

from app.domain.reflection_rollout import ReflectionRolloutPolicy
from app.repositories.memory_reflection_rollout_repository import InMemoryReflectionRolloutRepository
from app.services.reflection_rollout_service import ReflectionRolloutService


def _passing_gate(tmp_path):
    path = tmp_path / "reflection-gate.json"
    path.write_text(
        json.dumps(
            {
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
        ),
        encoding="utf-8",
    )
    return path


def test_tenant_canary_resolution_is_stable_and_isolated(tmp_path):
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository)
    policy = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="2.0.0", mode="enforced"),
    )
    service.start_canary(policy.id, tenant_id="tenant-a", actor_id="admin", percentage=40)

    first = service.resolve(
        tenant_id="tenant-a",
        subject="user-17",
        default_mode="shadow",
        gate_report_path=_passing_gate(tmp_path),
    )
    second = service.resolve(
        tenant_id="tenant-a",
        subject="user-17",
        default_mode="shadow",
        gate_report_path=_passing_gate(tmp_path),
    )
    other_tenant = service.resolve(
        tenant_id="tenant-b",
        subject="user-17",
        default_mode="shadow",
        gate_report_path=_passing_gate(tmp_path),
    )

    assert first.bucket == second.bucket
    assert first.policy_version == second.policy_version
    assert first.deployment_state in {"canary", "default"}
    assert other_tenant.policy_version == "default"
    assert other_tenant.effective_mode == "shadow"


def test_enforced_tenant_policy_still_fails_closed_without_offline_gate(tmp_path):
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository)
    policy = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="2.0.0", mode="enforced"),
    )
    service.publish(policy.id, tenant_id="tenant-a", actor_id="admin")

    decision = service.resolve(
        tenant_id="tenant-a",
        subject="user-1",
        default_mode="off",
        gate_report_path=tmp_path / "missing.json",
    )

    assert decision.requested_mode == "enforced"
    assert decision.effective_mode == "shadow"
    assert decision.gate_passed is False
    assert decision.reason == "offline_gate_missing_or_failed"


def test_rollback_reactivates_previous_policy_version(tmp_path):
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository)
    first = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="1.0.0", mode="shadow"),
    )
    service.publish(first.id, tenant_id="tenant-a", actor_id="admin")
    second = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="2.0.0", mode="enforced"),
    )
    service.publish(second.id, tenant_id="tenant-a", actor_id="admin")

    restored = service.rollback(second.id, tenant_id="tenant-a", actor_id="admin")
    decision = service.resolve(
        tenant_id="tenant-a",
        subject="user-1",
        default_mode="off",
        gate_report_path=_passing_gate(tmp_path),
    )

    assert restored.id == first.id
    assert restored.state == "active"
    assert decision.policy_version == "1.0.0"
    assert decision.effective_mode == "shadow"


def test_query_runtime_can_use_tenant_rollout_decision(tmp_path):
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository)
    policy = service.create_version(
        tenant_id="tenant-query",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="3.0.0", mode="enforced"),
    )
    service.publish(policy.id, tenant_id="tenant-query", actor_id="admin")

    decision = service.resolve(
        tenant_id="tenant-query",
        subject="analyst-9",
        default_mode="off",
        gate_report_path=_passing_gate(tmp_path),
    )

    assert decision.policy_version == "3.0.0"
    assert decision.deployment_state == "active"
    assert decision.effective_mode == "enforced"


def test_health_threshold_breach_rolls_back_active_policy():
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository)
    first = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(version="1.0.0", mode="shadow"),
    )
    service.publish(first.id, tenant_id="tenant-a", actor_id="admin")
    second = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin",
        policy=ReflectionRolloutPolicy(
            version="2.0.0",
            mode="enforced",
            max_error_acceptance_rate=0.01,
            max_invalid_retry_rate=0.05,
            max_p95_latency_ms=1000,
        ),
    )
    service.publish(second.id, tenant_id="tenant-a", actor_id="admin")

    result = service.evaluate_health(
        second.id,
        tenant_id="tenant-a",
        actor_id="monitor",
        metrics={
            "error_acceptance_rate": 0.02,
            "invalid_retry_rate": 0.01,
            "p95_latency_ms": 800,
            "security_permission_bypasses": 0,
        },
    )

    assert result["rolled_back"] is True
    assert result["reason_codes"] == ["ERROR_ACCEPTANCE_RATE_EXCEEDED"]
    assert result["active_version"] == "1.0.0"


def test_security_bypass_always_triggers_rollout_rollback():
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository)
    first = service.create_version(tenant_id="tenant-a", actor_id="admin", policy=ReflectionRolloutPolicy(version="1.0.0", mode="shadow"))
    service.publish(first.id, tenant_id="tenant-a", actor_id="admin")
    second = service.create_version(tenant_id="tenant-a", actor_id="admin", policy=ReflectionRolloutPolicy(version="2.0.0", mode="enforced"))
    service.publish(second.id, tenant_id="tenant-a", actor_id="admin")

    result = service.evaluate_health(
        second.id,
        tenant_id="tenant-a",
        actor_id="monitor",
        metrics={"security_permission_bypasses": 1},
    )

    assert result["rolled_back"] is True
    assert "SECURITY_PERMISSION_BYPASS" in result["reason_codes"]


def test_rollout_lifecycle_emits_redacted_audit_events():
    records = []
    repository = InMemoryReflectionRolloutRepository()
    service = ReflectionRolloutService(repository=repository, audit_writer=lambda **payload: records.append(payload))
    version = service.create_version(
        tenant_id="tenant-a",
        actor_id="admin-1",
        policy=ReflectionRolloutPolicy(version="1.0.0", mode="shadow"),
    )
    service.start_canary(version.id, tenant_id="tenant-a", actor_id="admin-1", percentage=25)
    service.publish(version.id, tenant_id="tenant-a", actor_id="admin-1")

    assert [item["event"] for item in records] == [
        "reflection_rollout_created",
        "reflection_rollout_canary_started",
        "reflection_rollout_published",
    ]
    assert all(item["tenant_id"] == "tenant-a" for item in records)
    assert all(item["actor_id"] == "admin-1" for item in records)
    assert all("policy" not in item and "metrics" not in item for item in records)

