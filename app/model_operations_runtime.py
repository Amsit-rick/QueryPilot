from app.config import settings
from app.repositories.memory_model_operations_repository import InMemoryModelOperationsRepository
from app.repositories.mysql_model_operations_repository import MySQLModelOperationsRepository
from app.services.model_operations_service import ModelOperationsService

_memory_repository = InMemoryModelOperationsRepository()
_mysql_repository = MySQLModelOperationsRepository()


def get_model_operations_service() -> ModelOperationsService:
    repository = _mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository
    return ModelOperationsService(repository)


def reset_memory_model_operations() -> None:
    _memory_repository.clear()
