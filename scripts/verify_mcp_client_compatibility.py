"""Verify Query Pilot's local MCP server with the pinned official Python client."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import secrets
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import anyio
import httpx2
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.mcp_client_compatibility import (  # noqa: E402
    CompatibilityCheck,
    TransportVerification,
    build_compatibility_matrix,
    verify_client_cancellation,
)


EXPECTED_TOOLS = {
    "schema.list",
    "query.plan",
    "task.status",
    "result.get",
    "skill.route",
}
EXPECTED_TEMPLATE = "querypilot://skills/catalog/{tenant_id}"


def _client_info() -> types.Implementation:
    return types.Implementation(name="query-pilot-compatibility", version="1.0.0")


def _server_environment(token: str, *, include_stdio_token: bool) -> dict[str, str]:
    environment = {
        "AUTH_ENABLED": "true",
        "API_KEYS": token,
        "DATABASE_BACKEND": "sqlite",
        "DEFAULT_DATASOURCE": "sqlite-demo",
        "PYTHONUTF8": "1",
    }
    if include_stdio_token:
        environment["QUERY_PILOT_MCP_TOKEN"] = token
    return environment


def _check(name: str, condition: bool, passed: str, failed: str) -> CompatibilityCheck:
    return CompatibilityCheck(name, "passed" if condition else "failed", passed if condition else failed)


async def _probe_authenticated_session(session: ClientSession) -> tuple[str, tuple[CompatibilityCheck, ...]]:
    checks: list[CompatibilityCheck] = []
    initialized = await session.initialize()
    checks.append(
        _check(
            "initialize",
            bool(initialized.protocol_version),
            f"negotiated protocol {initialized.protocol_version}",
            "protocol version was empty",
        )
    )

    tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    annotations_ok = all(
        tool.annotations is not None
        and tool.annotations.read_only_hint is True
        and tool.annotations.destructive_hint is False
        for tool in tools.tools
    )
    checks.append(
        _check(
            "tools/list",
            names == EXPECTED_TOOLS and annotations_ok,
            "listed five governed read-only tools with non-destructive annotations",
            f"unexpected tool contract: {sorted(names)}",
        )
    )

    templates = await session.list_resource_templates()
    template_names = {item.uri_template for item in templates.resource_templates}
    checks.append(
        _check(
            "resources/templates/list",
            template_names == {EXPECTED_TEMPLATE},
            "listed the tenant-scoped Skill catalog template",
            f"unexpected resource templates: {sorted(template_names)}",
        )
    )

    resource = await session.read_resource("querypilot://skills/catalog/default")
    resource_payload = json.loads(resource.contents[0].text)
    checks.append(
        _check(
            "resources/read",
            resource_payload.get("read_only") is True and bool(resource_payload.get("skills")),
            "read tenant-scoped Skill catalog JSON",
            "Skill catalog was missing read-only metadata or skills",
        )
    )

    call = await session.call_tool("skill.route", {"question": "统计待回款金额"})
    call_payload = call.structured_content or json.loads(call.content[0].text)
    checks.append(
        _check(
            "tools/call",
            not call.is_error and call_payload.get("selected_skill", {}).get("id") == "receivables-analysis",
            "called skill.route and received the deterministic receivables Skill",
            "skill.route did not return the expected structured result",
        )
    )

    invalid = await session.call_tool("query.plan", {"question": " "})
    invalid_text = "\n".join(
        item.text for item in invalid.content if getattr(item, "type", None) == "text"
    )
    checks.append(
        _check(
            "invalid_params",
            bool(invalid.is_error) and "E_INTEGRATION_QUESTION" in invalid_text,
            "invalid tool parameters returned stable code E_INTEGRATION_QUESTION",
            "invalid tool parameters did not return the stable application error code",
        )
    )
    checks.append(await verify_client_cancellation(session))
    return initialized.protocol_version, tuple(checks)


async def _probe_unauthenticated_session(session: ClientSession) -> CompatibilityCheck:
    await session.initialize()
    result = await session.call_tool("skill.route", {"question": "统计待回款金额"})
    result_text = "\n".join(
        item.text for item in result.content if getattr(item, "type", None) == "text"
    )
    return _check(
        "authentication",
        bool(result.is_error) and "E_MCP_AUTH" in result_text,
        "missing credentials returned stable code E_MCP_AUTH without exposing a secret",
        "missing credentials did not return the stable authentication error code",
    )


@asynccontextmanager
async def _stdio_session(
    python_executable: str,
    token: str,
    *,
    authenticated: bool,
) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(
        command=python_executable,
        args=[str(PROJECT_ROOT / "scripts" / "run_mcp_server.py"), "--transport", "stdio"],
        cwd=PROJECT_ROOT,
        env=_server_environment(token, include_stdio_token=authenticated),
        encoding="utf-8",
    )
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as streams:
            async with ClientSession(*streams, client_info=_client_info(), read_timeout_seconds=15) as session:
                yield session


async def verify_stdio(python_executable: str, token: str, sdk_version: str) -> tuple[str, TransportVerification]:
    async with _stdio_session(python_executable, token, authenticated=True) as session:
        protocol_version, checks = await _probe_authenticated_session(session)
    async with _stdio_session(python_executable, token, authenticated=False) as session:
        auth_check = await _probe_unauthenticated_session(session)
    return protocol_version, TransportVerification(
        client="official-python-sdk",
        client_version=sdk_version,
        transport="stdio",
        checks=checks + (auth_check,),
        limitations=(
            "验证客户端任务取消与进程清理；未使用真实长查询证明服务端业务处理已中断。",
        ),
    )


def _free_port(host: str) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as server:
        server.bind((host, 0))
        return int(server.getsockname()[1])


async def _wait_for_port(process: subprocess.Popen[Any], host: str, port: int) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"MCP HTTP server exited before readiness with code {process.returncode}")
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            await anyio.sleep(0.05)
    raise TimeoutError("MCP HTTP server did not become ready")


@asynccontextmanager
async def _http_server(
    python_executable: str,
    token: str,
    host: str,
    port: int,
) -> AsyncIterator[str]:
    environment = os.environ.copy()
    environment.update(_server_environment(token, include_stdio_token=False))
    process = subprocess.Popen(
        [
            python_executable,
            str(PROJECT_ROOT / "scripts" / "run_mcp_server.py"),
            "--transport",
            "streamable-http",
            "--host",
            host,
            "--port",
            str(port),
            "--path",
            "/mcp",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_for_port(process, host, port)
        yield f"http://{host}:{port}/mcp"
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                await anyio.to_thread.run_sync(process.wait, 5)
            except subprocess.TimeoutExpired:
                process.kill()
                await anyio.to_thread.run_sync(process.wait)


@asynccontextmanager
async def _http_session(url: str, token: str | None) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=15,
        follow_redirects=False,
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client, terminate_on_close=True) as streams:
            async with ClientSession(*streams, client_info=_client_info(), read_timeout_seconds=15) as session:
                yield session


async def verify_streamable_http(
    python_executable: str,
    token: str,
    sdk_version: str,
    host: str,
    port: int,
) -> tuple[str, TransportVerification]:
    async with _http_server(python_executable, token, host, port) as url:
        async with _http_session(url, token) as session:
            protocol_version, checks = await _probe_authenticated_session(session)
        async with _http_session(url, None) as session:
            auth_check = await _probe_unauthenticated_session(session)
    return protocol_version, TransportVerification(
        client="official-python-sdk",
        client_version=sdk_version,
        transport="streamable-http",
        checks=checks + (auth_check,),
        limitations=(
            "仅在本机回环 HTTP 验证；未覆盖 TLS、反向代理、mTLS、企业 OAuth 或网络分区。",
            "验证客户端任务取消与传输关闭；未用真实长查询证明服务端业务处理已中断。",
        ),
    )


def _failed_verification(
    *,
    sdk_version: str,
    transport: str,
    error: Exception,
) -> TransportVerification:
    return TransportVerification(
        client="official-python-sdk",
        client_version=sdk_version,
        transport=transport,
        checks=(
            CompatibilityCheck(
                "probe",
                "failed",
                f"probe failed before completion ({type(error).__name__})",
            ),
        ),
        limitations=("该传输没有产生可接受的完整兼容证据。",),
    )


def _write_matrix(output: Path, matrix: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


async def _run(args: argparse.Namespace) -> int:
    sdk_version = importlib.metadata.version("mcp")
    token = secrets.token_urlsafe(32)
    verifications: list[TransportVerification] = []
    protocol_versions: list[str] = []

    if args.transport in {"all", "stdio"}:
        try:
            protocol, verification = await verify_stdio(args.python, token, sdk_version)
            protocol_versions.append(protocol)
            verifications.append(verification)
        except Exception as exc:
            verifications.append(
                _failed_verification(
                    sdk_version=sdk_version,
                    transport="stdio",
                    error=exc,
                )
            )
    if args.transport in {"all", "streamable-http"}:
        try:
            port = args.port or _free_port(args.host)
            protocol, verification = await verify_streamable_http(
                args.python,
                token,
                sdk_version,
                args.host,
                port,
            )
            protocol_versions.append(protocol)
            verifications.append(verification)
        except Exception as exc:
            verifications.append(
                _failed_verification(
                    sdk_version=sdk_version,
                    transport="streamable-http",
                    error=exc,
                )
            )

    protocol_version = protocol_versions[0] if protocol_versions else "unknown"
    if len(set(protocol_versions)) > 1:
        protocol_version = ",".join(sorted(set(protocol_versions)))
    matrix = build_compatibility_matrix(
        sdk_version=sdk_version,
        protocol_version=protocol_version,
        verifications=tuple(verifications),
    )
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    _write_matrix(output, matrix)
    print(json.dumps({"output": str(output), "summary": matrix["summary"]}, ensure_ascii=False))
    return 1 if matrix["summary"]["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Query Pilot MCP compatibility with the pinned official Python SDK"
    )
    parser.add_argument(
        "--transport",
        choices=("all", "stdio", "streamable-http"),
        default="all",
    )
    parser.add_argument("--output", default="data/mcp-client-compatibility.json")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 selects an ephemeral loopback port")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("compatibility verification only binds to a loopback host")
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    return anyio.run(_run, args)


if __name__ == "__main__":
    raise SystemExit(main())
