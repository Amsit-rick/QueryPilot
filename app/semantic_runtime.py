from app.config import settings
from app.repositories.memory_semantic_repository import InMemorySemanticRepository
from app.repositories.mysql_semantic_repository import MySQLSemanticRepository
from app.services.semantic_service import SemanticService

_memory_repository = InMemorySemanticRepository()
_mysql_repository = MySQLSemanticRepository()


def get_semantic_service() -> SemanticService:
    return SemanticService(_mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository)


def reset_memory_semantics() -> None:
    _memory_repository.clear()

