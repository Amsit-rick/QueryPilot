"""Run a safe local acceptance check for governed external MCP invocation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.mcp_invocation_runtime import get_mcp_invocation_service
from app.mcp_registry_runtime import get_mcp_registry_service


def _local_token(env_ref: str) -> tuple[str, bool]:
    configured = os.getenv(env_ref, "").strip()
    if configured:
        return configured, False
    api_key = next((item.strip() for item in settings.api_keys.split(",") if item.strip()), "")
    if not api_key:
        raise RuntimeError(f"set {env_ref} or configure at least one API_KEYS entry")
    os.environ[env_ref] = api_key
    return api_key, True


async def accept(endpoint: str, env_ref: str) -> dict:
    _token, injected = _local_token(env_ref)
    registry = get_mcp_registry_service()
    invocation = get_mcp_invocation_service()
    server = None
    try:
        server = registry.create_server(
            tenant_id="default",
            actor_id="mcp-runtime-acceptance",
            name="Local MCP runtime acceptance",
            transport="streamable-http",
            endpoint=endpoint,
            auth_env_ref=env_ref,
            allowed_tools=["skill.route"],
            enabled=True,
            read_only_only=True,
            allow_private_network=False,
            request_timeout_seconds=10,
            max_concurrency=1,
            daily_call_limit=5,
            circuit_failure_threshold=2,
            circuit_reset_seconds=10,
        )
        health = await invocation.check_health(
            server.id,
            "default",
            actor_id="mcp-runtime-acceptance",
        )
        if health.health_status != "healthy" or "skill.route" not in health.discovered_tools:
            raise RuntimeError(
                f"health check failed: status={health.health_status}, error={health.last_error_code}"
            )
        called = await invocation.invoke_tool(
            server.id,
            "default",
            actor_id="mcp-runtime-acceptance",
            tool_name="skill.route",
            arguments={"question": "分析销售趋势"},
        )
        result = called.get("result", {})
        if result.get("isError") is True or result.get("resultType") != "complete":
            raise RuntimeError("read-only MCP tool did not complete successfully")
        return {
            "status": "passed",
            "health_status": health.health_status,
            "tool": called["tool_name"],
            "discovered_tools": list(health.discovered_tools),
            "result_type": result.get("resultType"),
            "cleanup": "pending",
        }
    finally:
        if server is not None:
            registry.delete_server(
                server.id,
                "default",
                actor_id="mcp-runtime-acceptance",
            )
        if injected:
            os.environ.pop(env_ref, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accept the governed MCP runtime against a local server")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8011/mcp")
    parser.add_argument("--auth-env-ref", default="QUERY_PILOT_MCP_ACCEPTANCE_TOKEN")
    args = parser.parse_args(argv)
    payload = asyncio.run(accept(args.endpoint, args.auth_env_ref))
    payload["cleanup"] = "completed"
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
