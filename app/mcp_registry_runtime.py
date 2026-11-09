"""Runtime repository selection for the external MCP registry."""

from app.config import settings
from app.repositories.memory_mcp_registry_repository import InMemoryMcpRegistryRepository
from app.repositories.mysql_mcp_registry_repository import MySQLMcpRegistryRepository
from app.services.mcp_registry_service import McpRegistryService

_memory_repository = InMemoryMcpRegistryRepository()
_mysql_repository = MySQLMcpRegistryRepository()


def get_mcp_registry_service() -> McpRegistryService:
    repository = _mysql_repository if settings.database_backend.lower() == "mysql" else _memory_repository
    return McpRegistryService(repository)


def reset_memory_mcp_registry() -> None:
    _memory_repository.clear()

