"""Process-local runtime for platform-owned executable Skill profiles."""

from __future__ import annotations

from .agent.skill_workflow import ExecutableSkillWorkflowService


_service = ExecutableSkillWorkflowService()


def get_executable_skill_workflow_service() -> ExecutableSkillWorkflowService:
    return _service


__all__ = ["get_executable_skill_workflow_service"]
