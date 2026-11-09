"""Select the durable query admission repository for the active backend."""
from app.config import settings
from app.repositories.memory_query_admission_repository import InMemoryQueryAdmissionRepository
from app.repositories.mysql_query_admission_repository import MySQLQueryAdmissionRepository
from app.services.query_admission_service import QueryAdmissionService


_memory_repository = InMemoryQueryAdmissionRepository()
_mysql_repository = MySQLQueryAdmissionRepository()


def get_query_admission_service() -> QueryAdmissionService:
    repository = _mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository
    return QueryAdmissionService(repository)


def reset_memory_query_admissions() -> None:
    _memory_repository.clear()


__all__ = ["get_query_admission_service", "reset_memory_query_admissions"]

