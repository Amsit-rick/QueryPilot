"""Shared task persistence wiring for API and workflow services."""
from app.config import settings
from app.repositories.memory_task_repository import InMemoryTaskRepository
from app.repositories.mysql_task_repository import MySQLTaskRepository
from app.services.task_service import TaskService

_memory_repository = InMemoryTaskRepository()
_mysql_repository = MySQLTaskRepository()


def get_task_service() -> TaskService:
    repository = _mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository
    return TaskService(repository)
