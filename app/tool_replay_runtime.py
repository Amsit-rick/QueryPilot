"""Shared replay policy registry."""
from app.services.tool_replay_policy_service import ToolReplayPolicyService


_service = ToolReplayPolicyService.default()


def get_tool_replay_policy_service() -> ToolReplayPolicyService:
    return _service


__all__ = ["get_tool_replay_policy_service"]

