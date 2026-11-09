"""Replay safety contracts for Agent and integration tool calls."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


POLICY_VERSION = "tool-replay-v1"


class ReplayClass(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    COMPENSATABLE = "compensatable"


class ReplayAction(str, Enum):
    REUSE_RESULT = "reuse_result"
    REPLAY = "replay"
    BLOCK = "block"
    COMPENSATE = "compensate"


@dataclass(frozen=True)
class ToolReplayPolicy:
    tool_name: str
    replay_class: ReplayClass
    idempotent: bool
    compensation_tool: str = ""
    registered: bool = True
    version: str = POLICY_VERSION

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "tool_name": self.tool_name,
            "replay_class": self.replay_class.value,
            "idempotent": self.idempotent,
            "compensation_tool": self.compensation_tool or None,
            "registered": self.registered,
        }


@dataclass(frozen=True)
class ToolReplayDecision:
    tool_name: str
    action: ReplayAction
    replay_class: ReplayClass
    error_code: str = ""
    compensation_tool: str = ""
    policy_version: str = POLICY_VERSION

    def as_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "action": self.action.value,
            "replay_class": self.replay_class.value,
            "error_code": self.error_code or None,
            "compensation_tool": self.compensation_tool or None,
            "policy_version": self.policy_version,
        }


__all__ = [
    "POLICY_VERSION",
    "ReplayAction",
    "ReplayClass",
    "ToolReplayDecision",
    "ToolReplayPolicy",
]

