"""Cached trace exporter wiring for local JSONL and optional OTLP export."""

from functools import lru_cache

from app.services.agent_trace_service import build_trace_exporter


@lru_cache(maxsize=8)
def get_agent_trace_exporter(
    mode: str,
    jsonl_path: str,
    otlp_endpoint: str,
    otlp_headers: str,
    service_name: str,
):
    return build_trace_exporter(
        mode=mode,
        jsonl_path=jsonl_path,
        otlp_endpoint=otlp_endpoint,
        otlp_headers=otlp_headers,
        service_name=service_name,
    )


__all__ = ["get_agent_trace_exporter"]
