from dataclasses import dataclass

from app.tool_replay_runtime import get_tool_replay_policy_service


@dataclass(frozen=True)
class RecoveryOutcome:
    task_id: str
    action: str
    status: str
    idempotency_key: str
    replay_action: str = ""
    blocked_tool: str = ""


class TaskRecoveryService:
    def __init__(self, tasks, *, replay_policies=None, **_kwargs):
        self.tasks = tasks
        self.replay_policies = replay_policies or get_tool_replay_policy_service()

    @staticmethod
    def _tool_for(action: str) -> tuple[str, str, bool]:
        if action == "completed_from_asset":
            return "query.execute", "completed", True
        if action == "failed_uncertain_result":
            return "query.execute", "unknown", False
        if action == "failed_uncertain_approval":
            return "approval.create", "unknown", False
        if action == "awaiting_approval":
            return "approval.create", "completed", True
        return "", "completed", True

    def recover(
        self,
        *,
        task_id,
        tenant_id,
        idempotency_key,
        approval_status="",
        fencing_token: int | None = None,
    ):
        task = self.tasks.repository.get(task_id, tenant_id)
        if not task:
            raise LookupError("E_TASK_NOT_FOUND:任务不存在")
        for event in self.tasks.repository.list_events(task_id):
            if event.kind == "task_recovery" and event.payload.get("idempotency_key") == idempotency_key:
                return RecoveryOutcome(
                    task_id,
                    event.payload["action"],
                    event.payload["status"],
                    idempotency_key,
                    str(event.payload.get("replay_action", "")),
                    str(event.payload.get("blocked_tool", "")),
                )

        assets = self.tasks.repository.list_artifacts(task_id)
        if task.status == "approval_required" and approval_status in ("", "pending"):
            action = "awaiting_approval"
        elif task.status == "approval_required":
            action = "failed_uncertain_approval"
        elif assets and task.status in ("running", "verifying"):
            action = "completed_from_asset"
        elif task.status in ("completed", "failed", "cancelled"):
            action = "already_terminal"
        else:
            action = "failed_uncertain_result"

        tool_name, invocation_status, result_available = self._tool_for(action)
        decision = (
            self.replay_policies.decide(
                tool_name,
                invocation_status=invocation_status,
                result_available=result_available,
            )
            if tool_name
            else None
        )
        if action == "completed_from_asset":
            if fencing_token is None:
                self.tasks.transition(task_id, tenant_id, "completed")
            else:
                self.tasks.transition_recovered(
                    task_id=task_id,
                    tenant_id=tenant_id,
                    status="completed",
                    fencing_token=fencing_token,
                )
        elif action in ("failed_uncertain_result", "failed_uncertain_approval"):
            if fencing_token is None:
                self.tasks.transition(task_id, tenant_id, "failed")
            else:
                self.tasks.transition_recovered(
                    task_id=task_id,
                    tenant_id=tenant_id,
                    status="failed",
                    fencing_token=fencing_token,
                )

        task = self.tasks.repository.get(task_id, tenant_id)
        replay_action = decision.action.value if decision else ""
        blocked_tool = tool_name if decision and decision.action.value == "block" else ""
        self.tasks.append_event(
            task_id=task_id,
            kind="task_recovery",
            payload={
                "idempotency_key": idempotency_key,
                "action": action,
                "status": task.status,
                "replay_action": replay_action,
                "blocked_tool": blocked_tool,
                "replay_policy_version": decision.policy_version if decision else "tool-replay-v1",
            },
        )
        return RecoveryOutcome(
            task_id,
            action,
            task.status,
            idempotency_key,
            replay_action,
            blocked_tool,
        )
