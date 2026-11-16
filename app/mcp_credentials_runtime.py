"""Runtime singleton for MCP secret references and OAuth tokens."""

from app.services.mcp_credentials import McpCredentialService


_service = McpCredentialService()


def get_mcp_credential_service() -> McpCredentialService:
    return _service


__all__ = ["get_mcp_credential_service"]
