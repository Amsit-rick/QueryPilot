"""Bounded, fail-closed convergence for stale non-terminal data tasks."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Callable

from app.domain.agent_recovery import AgentCheckpoint, RecoveryLeaseError
from app.domain.recovery_supervisor import (
    RECOVERABLE_TASK_STATUSES,
    RecoveryBatchReport,
    RecoveryDecision,
)
from app.domain.tool_replay import ReplayAction


class RecoverySupervisorService:
    def __init__(
        self,
        *,
        tasks,
        admissions,
        leases,
        task_recovery,
        replay_policies,
        now: Callable[[], datetime] | None = None,
        resume_safe_node: Callable | None = None,
    ):
        self.tasks = tasks
        self.admissions = admissions
        self.leases = leases
        self.task_recovery = task_recovery
        self.replay_policies = replay_policies
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._resume_safe_node = resume_safe_node

    def run_once(
        self,
        *,
        limit: int = 50,
        stale_after_seconds: int = 120,
        holder_id: str,
    ) -> RecoveryBatchReport:
        holder_id = str(holder_id).strip()
        if not holder_id:
            raise ValueError("E_RECOVERY_HOLDER_REQUIRED:恢复执行者不能为空")
        safe_limit = max(1, min(int(limit), 500))
        safe_stale = max(0, int(stale_after_seconds))
        cutoff = self._aware_now() - timedelta(seconds=safe_stale)
        candidates = self.tasks.list_recoverable(
            statuses=RECOVERABLE_TASK_STATUSES,
            updated_before=cutoff,
            limit=safe_limit,
        )
        decisions = tuple(
            self._recover_candidate(task=task, holder_id=holder_id)
            for task in candidates
        )
        skipped_actions = {
            "skip_lease_held",
            "skip_stale_fence",
            "await_approval",
            "resume_safe_node",
            "require_compensation",
        }
        failed_actions = {"error_isolated", "fail_uncertain_side_effect", "fail_invalid_checkpoint", "fail_from_admission"}
        return RecoveryBatchReport(
            scanned=len(candidates),
            recovered=sum(item.action not in skipped_actions | {"error_isolated"} for item in decisions),
            skipped=sum(item.action in skipped_actions for item in decisions),
            failed=sum(item.action in failed_actions for item in decisions),
            decisions=decisions,
        )

    def _aware_now(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _recover_candidate(self, *, task, holder_id: str) -> RecoveryDecision:
        try:
            lease = self.leases.acquire(
                task_id=task.id,
                tenant_id=task.tenant_id,
                holder_id=holder_id,
            )
        except RecoveryLeaseError as exc:
            return RecoveryDecision(
                task_id=task.id,
                tenant_id=task.tenant_id,
                prior_status=task.status,
                action="skip_lease_held",
                status=task.status,
                error_code=exc.code,
            )
        except Exception:
            return RecoveryDecision(
                task_id=task.id,
                tenant_id=task.tenant_id,
                prior_status=task.status,
                action="error_isolated",
                status=task.status,
                error_code="E_RECOVERY_LEASE_BACKEND",
            )

        try:
            if not self.tasks.claim_recovery_fence(
                task_id=task.id,
                tenant_id=task.tenant_id,
                fencing_token=lease.fencing_token,
            ):
                return RecoveryDecision(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    prior_status=task.status,
                    action="skip_stale_fence",
                    status=task.status,
                    error_code="E_RECOVERY_FENCING_STALE",
                    fencing_token=lease.fencing_token,
                )
            return self._recover_with_lease(task=task, lease=lease)
        except RecoveryLeaseError as exc:
            if exc.code in {"E_RECOVERY_FENCING_STALE", "E_RECOVERY_LEASE_HELD"}:
                return RecoveryDecision(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    prior_status=task.status,
                    action="skip_stale_fence",
                    status=self._current_status(task),
                    error_code="E_RECOVERY_FENCING_STALE",
                    fencing_token=lease.fencing_token,
                )
            try:
                return self._fail_invalid_checkpoint(
                    task=task,
                    lease=lease,
                    error_code=exc.code,
                )
            except RecoveryLeaseError:
                return RecoveryDecision(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    prior_status=task.status,
                    action="skip_stale_fence",
                    status=self._current_status(task),
                    error_code="E_RECOVERY_FENCING_STALE",
                    fencing_token=lease.fencing_token,
                )
        except Exception:
            decision = RecoveryDecision(
                task_id=task.id,
                tenant_id=task.tenant_id,
                prior_status=task.status,
                action="error_isolated",
                status=self._current_status(task),
                error_code="E_RECOVERY_SUPERVISOR_ITEM",
                fencing_token=lease.fencing_token,
            )
            try:
                self._append_supervisor_event(
                    task=task,
                    lease=lease,
                    decision=decision,
                    idempotency_key=self._idempotency_key(task=task),
                )
            except RecoveryLeaseError:
                return RecoveryDecision(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    prior_status=task.status,
                    action="skip_stale_fence",
                    status=self._current_status(task),
                    error_code="E_RECOVERY_FENCING_STALE",
                    fencing_token=lease.fencing_token,
                )
            return decision
        finally:
            try:
                self.leases.release(lease)
            except RecoveryLeaseError:
                pass

    def _recover_with_lease(self, *, task, lease) -> RecoveryDecision:
        checkpoint = self.leases.load(task_id=task.id, tenant_id=task.tenant_id)
        if checkpoint is not None and (
            checkpoint.task_id != task.id or checkpoint.tenant_id != task.tenant_id
        ):
            raise RecoveryLeaseError(
                "E_RECOVERY_IDENTITY_MISMATCH",
                "checkpoint 与任务身份不一致",
            )
        admission = self.admissions.find_by_task_id(
            tenant_id=task.tenant_id,
            task_id=task.id,
        )
        detail = self.tasks.detail_for_tenant(task_id=task.id, tenant_id=task.tenant_id)
        steps = detail["steps"]
        artifacts = detail["artifacts"]
        tool_name = self._tool_name(checkpoint=checkpoint, steps=steps)
        checkpoint_node = checkpoint.node_cursor if checkpoint else ""
        admission_status = admission.status if admission else ""
        idempotency_key = self._idempotency_key(
            task=task,
            checkpoint=checkpoint,
            admission_status=admission_status,
            tool_name=tool_name,
        )
        existing = self._existing_decision(task.id, idempotency_key)
        if existing is not None:
            return existing

        if task.status == "approval_required":
            approval_steps = [step for step in steps if step.kind == "approval"]
            approval_status = approval_steps[-1].status if approval_steps else "unknown"
            if approval_status == "pending":
                outcome = self.task_recovery.recover(
                    task_id=task.id,
                    tenant_id=task.tenant_id,
                    idempotency_key=idempotency_key,
                    approval_status="pending",
                    fencing_token=lease.fencing_token,
                )
                decision = self._decision(
                    task=task,
                    lease=lease,
                    action="await_approval",
                    checkpoint_node=checkpoint_node,
                    admission_status=admission_status,
                    replay_action=outcome.replay_action,
                    tool_name="approval.create",
                )
            else:
                decision = self._fail_task(
                    task=task,
                    lease=lease,
                    action="fail_uncertain_side_effect",
                    error_code="E_RECOVERY_APPROVAL_UNCERTAIN",
                    checkpoint_node=checkpoint_node,
                    admission_status=admission_status,
                    replay_action="block",
                    tool_name="approval.create",
                )
        elif admission is not None and admission.status == "failed":
            decision = self._fail_task(
                task=task,
                lease=lease,
                action="fail_from_admission",
                error_code=admission.error_code or "E_QUERY_FAILED",
                checkpoint_node=checkpoint_node,
                admission_status=admission_status,
                tool_name=tool_name,
            )
        elif artifacts and task.status in {"running", "verifying"}:
            outcome = self.task_recovery.recover(
                task_id=task.id,
                tenant_id=task.tenant_id,
                idempotency_key=idempotency_key,
                fencing_token=lease.fencing_token,
            )
            decision = self._decision(
                task=task,
                lease=lease,
                action="reuse_existing_result",
                checkpoint_node=checkpoint_node,
                admission_status=admission_status,
                replay_action=outcome.replay_action,
                tool_name="query.execute",
            )
        elif tool_name:
            replay = self.replay_policies.decide(
                tool_name,
                invocation_status="unknown",
                result_available=False,
            )
            if replay.action == ReplayAction.REPLAY:
                if self._resume_safe_node is not None:
                    self._resume_safe_node(
                        task,
                        checkpoint,
                        replay,
                        lease.fencing_token,
                    )
                decision = self._decision(
                    task=task,
                    lease=lease,
                    action="resume_safe_node",
                    checkpoint_node=checkpoint_node,
                    admission_status=admission_status,
                    replay_action=replay.action.value,
                    tool_name=tool_name,
                )
            elif replay.action == ReplayAction.COMPENSATE:
                decision = self._decision(
                    task=task,
                    lease=lease,
                    action="require_compensation",
                    error_code=replay.error_code,
                    checkpoint_node=checkpoint_node,
                    admission_status=admission_status,
                    replay_action=replay.action.value,
                    tool_name=tool_name,
                )
            else:
                decision = self._fail_task(
                    task=task,
                    lease=lease,
                    action="fail_uncertain_side_effect",
                    error_code=replay.error_code or "E_TOOL_REPLAY_UNSAFE",
                    checkpoint_node=checkpoint_node,
                    admission_status=admission_status,
                    replay_action=replay.action.value,
                    tool_name=tool_name,
                )
        else:
            decision = self._fail_task(
                task=task,
                lease=lease,
                action="fail_uncertain_side_effect",
                error_code="E_RECOVERY_STATE_UNCERTAIN",
                checkpoint_node=checkpoint_node,
                admission_status=admission_status,
            )

        self._append_supervisor_event(
            task=task,
            lease=lease,
            decision=decision,
            idempotency_key=idempotency_key,
        )
        return decision

    def _fail_invalid_checkpoint(self, *, task, lease, error_code: str) -> RecoveryDecision:
        idempotency_key = self._idempotency_key(task=task, checkpoint_error=error_code)
        existing = self._existing_decision(task.id, idempotency_key)
        if existing is not None:
            return existing
        decision = self._fail_task(
            task=task,
            lease=lease,
            action="fail_invalid_checkpoint",
            error_code=error_code,
        )
        self._append_supervisor_event(
            task=task,
            lease=lease,
            decision=decision,
            idempotency_key=idempotency_key,
        )
        return decision

    def _fail_task(self, *, task, lease, action: str, error_code: str, **values) -> RecoveryDecision:
        current = self.tasks.repository.get(task.id, task.tenant_id)
        if current is not None and current.status not in {"completed", "failed", "cancelled"}:
            self.tasks.transition_recovered(
                task_id=task.id,
                tenant_id=task.tenant_id,
                status="failed",
                fencing_token=lease.fencing_token,
            )
        return self._decision(
            task=task,
            lease=lease,
            action=action,
            error_code=error_code,
            **values,
        )

    def _decision(self, *, task, lease, action: str, **values) -> RecoveryDecision:
        return RecoveryDecision(
            task_id=task.id,
            tenant_id=task.tenant_id,
            prior_status=task.status,
            action=action,
            status=self._current_status(task),
            fencing_token=lease.fencing_token,
            **values,
        )

    def _current_status(self, task) -> str:
        current = self.tasks.repository.get(task.id, task.tenant_id)
        return current.status if current is not None else task.status

    @staticmethod
    def _tool_name(*, checkpoint: AgentCheckpoint | None, steps) -> str:
        if checkpoint is not None:
            replay = checkpoint.snapshot.get("tool_replay")
            if isinstance(replay, dict) and replay.get("tool_name"):
                return str(replay["tool_name"])
        for step in reversed(steps):
            replay = step.output.get("tool_replay") if isinstance(step.output, dict) else None
            if isinstance(replay, dict) and replay.get("tool_name"):
                return str(replay["tool_name"])
        return ""

    @staticmethod
    def _idempotency_key(
        *,
        task,
        checkpoint: AgentCheckpoint | None = None,
        admission_status: str = "",
        tool_name: str = "",
        checkpoint_error: str = "",
    ) -> str:
        payload = {
            "task_id": task.id,
            "tenant_id": task.tenant_id,
            "status": task.status,
            "updated_at": task.updated_at.isoformat(),
            "checkpoint_execution": checkpoint.execution_id if checkpoint else "",
            "checkpoint_node": checkpoint.node_cursor if checkpoint else "",
            "admission_status": admission_status,
            "tool_name": tool_name,
            "checkpoint_error": checkpoint_error,
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        return f"recovery-supervisor:{digest}"

    def _append_supervisor_event(self, *, task, lease, decision: RecoveryDecision, idempotency_key: str) -> None:
        payload = asdict(decision)
        if not self.tasks.append_recovery_event(
            task_id=task.id,
            tenant_id=task.tenant_id,
            fencing_token=lease.fencing_token,
            idempotency_key=idempotency_key,
            payload=payload,
        ):
            raise RecoveryLeaseError(
                "E_RECOVERY_FENCING_STALE",
                "恢复事件未由当前 fencing token 提交",
            )

    def _existing_decision(self, task_id: str, idempotency_key: str) -> RecoveryDecision | None:
        for event in self.tasks.repository.list_events(task_id):
            if event.kind != "recovery_supervisor" or event.payload.get("idempotency_key") != idempotency_key:
                continue
            values = {
                key: value
                for key, value in event.payload.items()
                if key in RecoveryDecision.__dataclass_fields__
            }
            return RecoveryDecision(**values)
        return None


__all__ = ["RecoverySupervisorService"]
