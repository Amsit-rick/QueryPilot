"""Fail-closed replay decisions for Agent tools and side effects."""
from __future__ import annotations

from app.domain.tool_replay import (
    ReplayAction,
    ReplayClass,
    ToolReplayDecision,
    ToolReplayPolicy,
)


class ToolReplayPolicyService:
    def __init__(self, policies: list[ToolReplayPolicy]):
        self._policies = {policy.tool_name: policy for policy in policies}

    @classmethod
    def default(cls) -> "ToolReplayPolicyService":
        return cls(
            [
                ToolReplayPolicy("schema.inspect", ReplayClass.SAFE, idempotent=True),
                ToolReplayPolicy("query.validate", ReplayClass.SAFE, idempotent=True),
                ToolReplayPolicy("query.explain", ReplayClass.SAFE, idempotent=True),
                ToolReplayPolicy("query.execute", ReplayClass.UNSAFE, idempotent=False),
                ToolReplayPolicy("approval.create", ReplayClass.UNSAFE, idempotent=False),
                ToolReplayPolicy(
                    "export.enqueue",
                    ReplayClass.COMPENSATABLE,
                    idempotent=False,
                    compensation_tool="export.cancel",
                ),
                ToolReplayPolicy("result.materialize", ReplayClass.SAFE, idempotent=True),
            ]
        )

    def require(self, tool_name: str) -> ToolReplayPolicy:
        policy = self._policies.get(tool_name)
        if policy is None:
            raise LookupError(f"E_TOOL_REPLAY_POLICY_NOT_FOUND:未注册工具 {tool_name}")
        return policy

    def resolve(self, tool_name: str) -> ToolReplayPolicy:
        policy = self._policies.get(tool_name)
        if policy is not None:
            return policy
        return ToolReplayPolicy(
            tool_name=tool_name,
            replay_class=ReplayClass.UNSAFE,
            idempotent=False,
            registered=False,
        )

    @staticmethod
    def for_mcp_tool(descriptor) -> ToolReplayPolicy:
        safe = (
            descriptor.read_only is True
            and descriptor.destructive is not True
            and descriptor.idempotent is True
        )
        return ToolReplayPolicy(
            tool_name=f"mcp.invoke:{descriptor.name}",
            replay_class=ReplayClass.SAFE if safe else ReplayClass.UNSAFE,
            idempotent=safe,
        )

    def decide(
        self,
        tool_name: str,
        *,
        invocation_status: str,
        result_available: bool,
        compensation_completed: bool = False,
    ) -> ToolReplayDecision:
        policy = self.resolve(tool_name)
        if invocation_status == "completed" and result_available:
            return ToolReplayDecision(
                tool_name=tool_name,
                action=ReplayAction.REUSE_RESULT,
                replay_class=policy.replay_class,
            )
        if invocation_status == "not_started":
            return ToolReplayDecision(
                tool_name=tool_name,
                action=ReplayAction.REPLAY,
                replay_class=policy.replay_class,
            )
        if policy.replay_class == ReplayClass.SAFE:
            return ToolReplayDecision(
                tool_name=tool_name,
                action=ReplayAction.REPLAY,
                replay_class=policy.replay_class,
            )
        if policy.replay_class == ReplayClass.COMPENSATABLE:
            return ToolReplayDecision(
                tool_name=tool_name,
                action=ReplayAction.REPLAY if compensation_completed else ReplayAction.COMPENSATE,
                replay_class=policy.replay_class,
                error_code="" if compensation_completed else "E_TOOL_COMPENSATION_REQUIRED",
                compensation_tool=policy.compensation_tool,
            )
        return ToolReplayDecision(
            tool_name=tool_name,
            action=ReplayAction.BLOCK,
            replay_class=policy.replay_class,
            error_code="E_TOOL_REPLAY_UNSAFE",
        )


__all__ = ["ToolReplayPolicyService"]

