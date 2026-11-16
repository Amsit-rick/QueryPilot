"""Runtime assembly for bounded MCP enrichment of Agent context."""

from app.config import settings
from app.mcp_invocation_runtime import get_mcp_invocation_service
from app.mcp_registry_runtime import get_mcp_registry_service
from app.services.mcp_agent_enrichment_service import (
    McpAgentEnrichmentService,
    McpEnrichmentLimits,
)


def get_mcp_agent_enrichment_service() -> McpAgentEnrichmentService:
    return McpAgentEnrichmentService(
        registry=get_mcp_registry_service(),
        invocation=get_mcp_invocation_service(),
        enabled=settings.mcp_agent_enrichment_enabled,
        limits=McpEnrichmentLimits(
            max_tools=settings.mcp_agent_enrichment_max_tools,
            max_result_bytes=settings.mcp_agent_enrichment_max_result_bytes,
            max_depth=settings.mcp_agent_enrichment_max_depth,
            max_fields=settings.mcp_agent_enrichment_max_fields,
            max_text_chars=settings.mcp_agent_enrichment_max_text_chars,
            max_question_chars=settings.mcp_agent_enrichment_max_question_chars,
        ),
    )


__all__ = ["get_mcp_agent_enrichment_service"]
