"""Tenant-scoped, stable-bucket reflection rollout governance."""

from __future__ import annotations

import hashlib

from app.domain.reflection_rollout import (
    ReflectionRolloutDecision,
    ReflectionRolloutPolicy,
    ReflectionRolloutVersion,
)
from app.repositories.memory_reflection_rollout_repository import InMemoryReflectionRolloutRepository
from app.services.reflection_mode_service import resolve_reflection_mode


class ReflectionRolloutService:
    def __init__(self, *, repository=None, audit_writer=None):
        self.repository = repository or InMemoryReflectionRolloutRepository()
        self.audit_writer = audit_writer

    def _audit(self, *, event: str, tenant_id: str, actor_id: str, version_id: str) -> None:
        if self.audit_writer is not None:
            self.audit_writer(
                event=event,
                tenant_id=tenant_id,
                actor_id=actor_id,
                version_id=version_id,
            )

    @staticmethod
    def _bucket(tenant_id: str, subject: str, version: str) -> int:
        value = f"{tenant_id}|{subject}|reflection|{version}".encode("utf-8")
        return int.from_bytes(hashlib.sha256(value).digest()[:8], "big") % 100

    def create_version(self, *, tenant_id: str, actor_id: str, policy: ReflectionRolloutPolicy) -> ReflectionRolloutVersion:
        created = self.repository.create(
            ReflectionRolloutVersion(tenant_id=tenant_id, policy=policy, created_by=actor_id)
        )
        self._audit(event="reflection_rollout_created", tenant_id=tenant_id, actor_id=actor_id, version_id=created.id)
        return created

    def start_canary(self, version_id: str, *, tenant_id: str, actor_id: str, percentage: int) -> ReflectionRolloutVersion:
        if percentage < 1 or percentage > 100:
            raise ValueError("E_REFLECTION_CANARY_PERCENT:灰度比例必须为 1 到 100")
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("反思灰度版本不存在")
        if current.state not in {"draft", "canary"}:
            raise ValueError("E_REFLECTION_CANARY_STATE:仅草稿或灰度版本可调整灰度")
        updated = self.repository.set_canary(version_id, tenant_id, percentage)
        self._audit(event="reflection_rollout_canary_started", tenant_id=tenant_id, actor_id=actor_id, version_id=version_id)
        return updated

    def publish(self, version_id: str, *, tenant_id: str, actor_id: str) -> ReflectionRolloutVersion:
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("反思灰度版本不存在")
        if current.state not in {"draft", "canary", "active"}:
            raise ValueError("E_REFLECTION_PUBLISH_STATE:已退役版本不能直接发布")
        updated = self.repository.activate(version_id, tenant_id)
        self._audit(event="reflection_rollout_published", tenant_id=tenant_id, actor_id=actor_id, version_id=version_id)
        return updated

    def rollback(self, version_id: str, *, tenant_id: str, actor_id: str) -> ReflectionRolloutVersion:
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("反思灰度版本不存在")
        if current.state != "active":
            raise ValueError("E_REFLECTION_ROLLBACK_STATE:只能回滚当前 active 版本")
        candidates = [item for item in self.repository.list(tenant_id) if item.id != version_id and item.state == "retired"]
        if not candidates:
            raise ValueError("E_REFLECTION_ROLLBACK_TARGET:没有可回滚的历史版本")
        self.repository.retire(version_id, tenant_id)
        restored = self.repository.activate(candidates[0].id, tenant_id)
        self._audit(event="reflection_rollout_rolled_back", tenant_id=tenant_id, actor_id=actor_id, version_id=version_id)
        return restored

    def evaluate_health(
        self,
        version_id: str,
        *,
        tenant_id: str,
        actor_id: str,
        metrics: dict,
    ) -> dict:
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("反思灰度版本不存在")
        reason_codes = []
        if float(metrics.get("error_acceptance_rate", 0) or 0) > current.policy.max_error_acceptance_rate:
            reason_codes.append("ERROR_ACCEPTANCE_RATE_EXCEEDED")
        if float(metrics.get("invalid_retry_rate", 0) or 0) > current.policy.max_invalid_retry_rate:
            reason_codes.append("INVALID_RETRY_RATE_EXCEEDED")
        if float(metrics.get("p95_latency_ms", 0) or 0) > current.policy.max_p95_latency_ms:
            reason_codes.append("P95_LATENCY_EXCEEDED")
        if int(metrics.get("security_permission_bypasses", 0) or 0) > 0:
            reason_codes.append("SECURITY_PERMISSION_BYPASS")
        if not reason_codes:
            return {
                "rolled_back": False,
                "reason_codes": [],
                "active_version": current.policy.version,
                "evaluated_version": current.policy.version,
            }
        restored = self.rollback(version_id, tenant_id=tenant_id, actor_id=actor_id)
        return {
            "rolled_back": True,
            "reason_codes": reason_codes,
            "active_version": restored.policy.version,
            "evaluated_version": current.policy.version,
        }

    def resolve(self, *, tenant_id: str, subject: str, default_mode: str, gate_report_path) -> ReflectionRolloutDecision:
        versions = self.repository.list(tenant_id)
        active = next((item for item in versions if item.state == "active"), None)
        canary = next((item for item in versions if item.state == "canary"), None)
        bucket = self._bucket(tenant_id, subject, canary.policy.version if canary else "default")
        selected = canary if canary and bucket < canary.canary_percent else active
        if selected is None:
            mode = resolve_reflection_mode(default_mode, report_path=gate_report_path)
            return ReflectionRolloutDecision(
                tenant_id=tenant_id,
                subject=subject,
                requested_mode=mode.requested_mode,
                effective_mode=mode.effective_mode,
                policy_version="default",
                deployment_state="default",
                bucket=bucket,
                gate_passed=mode.gate_passed,
                reason=mode.reason,
            )
        mode = resolve_reflection_mode(selected.policy.mode, report_path=gate_report_path)
        return ReflectionRolloutDecision(
            tenant_id=tenant_id,
            subject=subject,
            requested_mode=mode.requested_mode,
            effective_mode=mode.effective_mode,
            policy_version=selected.policy.version,
            deployment_state=selected.state,
            bucket=bucket,
            gate_passed=mode.gate_passed,
            reason=mode.reason,
        )


__all__ = ["ReflectionRolloutService"]

