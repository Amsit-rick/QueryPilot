"""Runtime repository selection for Skill release governance."""

from app.config import settings
from app.repositories.memory_skill_repository import InMemorySkillRepository
from app.repositories.mysql_skill_repository import MySQLSkillRepository
from app.services.skill_service import SkillService

_memory_repository = InMemorySkillRepository()
_mysql_repository = MySQLSkillRepository()


def get_skill_service() -> SkillService:
    repository = (
        _mysql_repository
        if settings.database_backend.lower() == "mysql"
        else _memory_repository
    )
    return SkillService(repository=repository)


def reset_memory_skill_repository() -> None:
    _memory_repository.clear()


__all__ = ["get_skill_service", "reset_memory_skill_repository"]
