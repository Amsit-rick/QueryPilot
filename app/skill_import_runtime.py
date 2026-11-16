"""Runtime singleton for controlled SKILL.md imports."""

from .services.skill_import_service import SkillImportService


_service = SkillImportService()


def get_skill_import_service() -> SkillImportService:
    return _service


__all__ = ["get_skill_import_service"]
