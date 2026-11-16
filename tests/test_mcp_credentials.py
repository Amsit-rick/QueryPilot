import asyncio
from dataclasses import replace

import pytest

from app.domain.mcp_registry import McpServerRegistration
from app.services.mcp_credentials import (
    EnvironmentSecretProvider,
    McpCredentialError,
    McpCredentialService,
)


def run(coro):
    return asyncio.run(coro)


def server(**overrides):
    payload = {
        "id": "server-1",
        "tenant_id": "tenant-a",
        "name": "Finance MCP",
        "transport": "streamable-http",
        "endpoint": "https://mcp.example.com/mcp",
        "auth_env_ref": "env://FINANCE_MCP_SECRET",
        "allowed_tools": ("report.read",),
        "auth_type": "bearer",
    }
    payload.update(overrides)
    return McpServerRegistration(**payload)


def test_environment_secret_provider_resolves_references_without_accepting_inline_values():
    provider = EnvironmentSecretProvider({"FINANCE_MCP_SECRET": "bearer-secret"})

    assert provider.is_configured("env://FINANCE_MCP_SECRET") is True
    assert provider.resolve("env://FINANCE_MCP_SECRET") == "bearer-secret"
    assert provider.resolve("FINANCE_MCP_SECRET") == "bearer-secret"

    with pytest.raises(McpCredentialError, match="E_MCP_SECRET_REF"):
        provider.resolve("inline-secret")
    with pytest.raises(McpCredentialError, match="E_MCP_SECRET_MISSING"):
        provider.resolve("env://MISSING_MCP_SECRET")


def test_oauth_client_credentials_are_cached_and_bound_to_tenant_server_and_version():
    calls = []
    now = [1000.0]

    async def request_token(url, form, timeout):
        calls.append((url, dict(form), timeout))
        return {"access_token": f"token-{len(calls)}", "token_type": "Bearer", "expires_in": 120}

    credentials = McpCredentialService(
        secret_provider=EnvironmentSecretProvider({"FINANCE_MCP_SECRET": "client-secret"}),
        token_request=request_token,
        clock=lambda: now[0],
        refresh_skew_seconds=30,
    )
    oauth = server(
        auth_type="oauth_client_credentials",
        oauth_token_url="https://identity.example.com/oauth/token",
        oauth_client_id="query-pilot",
        oauth_scopes=("mcp.read", "catalog.read"),
    )

    first = run(credentials.bearer_token(oauth))
    second = run(credentials.bearer_token(oauth))
    other_tenant = run(credentials.bearer_token(replace(oauth, tenant_id="tenant-b")))
    rotated = run(credentials.bearer_token(replace(oauth, credential_version=2)))

    assert first == second == "token-1"
    assert other_tenant == "token-2"
    assert rotated == "token-3"
    assert calls[0] == (
        "https://identity.example.com/oauth/token",
        {
            "grant_type": "client_credentials",
            "client_id": "query-pilot",
            "client_secret": "client-secret",
            "scope": "mcp.read catalog.read",
        },
        oauth.request_timeout_seconds,
    )


def test_oauth_refreshes_before_expiry_and_sanitizes_provider_failures():
    now = [1000.0]
    responses = [
        {"access_token": "short-lived", "token_type": "Bearer", "expires_in": 35},
        RuntimeError("upstream rejected client_secret=do-not-leak"),
    ]

    async def request_token(_url, _form, _timeout):
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    credentials = McpCredentialService(
        secret_provider=EnvironmentSecretProvider({"FINANCE_MCP_SECRET": "client-secret"}),
        token_request=request_token,
        clock=lambda: now[0],
        refresh_skew_seconds=30,
    )
    oauth = server(
        auth_type="oauth_client_credentials",
        oauth_token_url="https://identity.example.com/oauth/token",
        oauth_client_id="query-pilot",
    )

    assert run(credentials.bearer_token(oauth)) == "short-lived"
    now[0] = 1006.0
    with pytest.raises(McpCredentialError, match="E_MCP_OAUTH_UPSTREAM") as exc:
        run(credentials.bearer_token(oauth))
    assert "do-not-leak" not in str(exc.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": "secret-token", "token_type": "MAC", "expires_in": 60},
        {"access_token": "secret-token", "token_type": "Bearer", "expires_in": 0},
        {"access_token": {"nested": "secret-token"}, "token_type": "Bearer", "expires_in": 60},
        {"access_token": "secret-token", "token_type": "Bearer", "expires_in": True},
    ],
)
def test_invalid_oauth_token_responses_fail_closed_without_leaking_token(payload):
    async def request_token(_url, _form, _timeout):
        return payload

    credentials = McpCredentialService(
        secret_provider=EnvironmentSecretProvider({"FINANCE_MCP_SECRET": "client-secret"}),
        token_request=request_token,
    )
    oauth = server(
        auth_type="oauth_client_credentials",
        oauth_token_url="https://identity.example.com/oauth/token",
        oauth_client_id="query-pilot",
    )

    with pytest.raises(McpCredentialError, match="E_MCP_OAUTH_RESPONSE") as exc:
        run(credentials.bearer_token(oauth))
    assert "secret-token" not in str(exc.value)
