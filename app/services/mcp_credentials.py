"""Secret-reference resolution and OAuth client-credentials for external MCP."""

from __future__ import annotations

import math
import os
import re
import threading
import time
from typing import Any, Awaitable, Callable, Mapping

import httpx


_ENV_REFERENCE = re.compile(r"^(?:env://)?([A-Z][A-Z0-9_]{1,127})$")
_MAX_TOKEN_RESPONSE_BYTES = 64 * 1024


class McpCredentialError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"{code}:{message}")


class EnvironmentSecretProvider:
    """Resolve environment-backed references without persisting secret values."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = environ if environ is not None else os.environ

    @staticmethod
    def _name(reference: str) -> str:
        match = _ENV_REFERENCE.fullmatch(str(reference or "").strip())
        if match is None:
            raise McpCredentialError("E_MCP_SECRET_REF", "MCP 凭据引用格式无效")
        return match.group(1)

    def is_configured(self, reference: str) -> bool:
        try:
            name = self._name(reference)
        except McpCredentialError:
            return False
        return bool(str(self.environ.get(name, "")).strip())

    def resolve(self, reference: str) -> str:
        name = self._name(reference)
        value = str(self.environ.get(name, "")).strip()
        if not value:
            raise McpCredentialError("E_MCP_SECRET_MISSING", "MCP Server 凭据未配置")
        return value


TokenRequest = Callable[[str, dict[str, str], float], Awaitable[dict[str, Any]]]


async def _request_oauth_token(url: str, form: dict[str, str], timeout: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=max(1.0, float(timeout)),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                url,
                data=form,
                headers={"Accept": "application/json"},
            )
            if len(response.content) > _MAX_TOKEN_RESPONSE_BYTES:
                raise McpCredentialError("E_MCP_OAUTH_RESPONSE", "OAuth Token 响应超过大小限制")
            if response.status_code < 200 or response.status_code >= 300:
                raise McpCredentialError(
                    "E_MCP_OAUTH_UPSTREAM",
                    "OAuth Token Endpoint 拒绝了凭据请求",
                    retryable=response.status_code >= 500,
                )
            payload = response.json()
    except McpCredentialError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise McpCredentialError(
            "E_MCP_OAUTH_UPSTREAM",
            "OAuth Token Endpoint 暂时不可用",
            retryable=True,
        ) from exc
    except (ValueError, TypeError) as exc:
        raise McpCredentialError("E_MCP_OAUTH_RESPONSE", "OAuth Token 响应不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise McpCredentialError("E_MCP_OAUTH_RESPONSE", "OAuth Token 响应必须是对象")
    return payload


class McpCredentialService:
    def __init__(
        self,
        *,
        secret_provider: EnvironmentSecretProvider | None = None,
        token_request: TokenRequest | None = None,
        clock: Callable[[], float] | None = None,
        refresh_skew_seconds: int = 30,
        max_token_ttl_seconds: int = 86400,
    ):
        self.secret_provider = secret_provider or EnvironmentSecretProvider()
        self._token_request = token_request or _request_oauth_token
        self._clock = clock or time.time
        self.refresh_skew_seconds = max(0, int(refresh_skew_seconds))
        self.max_token_ttl_seconds = max(60, int(max_token_ttl_seconds))
        self._cache: dict[tuple[str, str, int], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def is_configured(self, server) -> bool:
        if not self.secret_provider.is_configured(server.auth_env_ref):
            return False
        if server.auth_type == "bearer":
            return True
        return bool(server.oauth_token_url and server.oauth_client_id)

    def invalidate(self, tenant_id: str, server_id: str) -> None:
        with self._lock:
            keys = [key for key in self._cache if key[:2] == (tenant_id, server_id)]
            for key in keys:
                token, _expires_at = self._cache.pop(key)
                del token

    async def bearer_token(self, server) -> str:
        if server.auth_type == "bearer":
            return self.secret_provider.resolve(server.auth_env_ref)
        if server.auth_type != "oauth_client_credentials":
            raise McpCredentialError("E_MCP_AUTH_TYPE", "MCP 认证类型不受支持")
        if not server.oauth_token_url or not server.oauth_client_id:
            raise McpCredentialError("E_MCP_OAUTH_CONFIG", "OAuth Client Credentials 配置不完整")
        key = (str(server.tenant_id), str(server.id), int(server.credential_version))
        now = float(self._clock())
        with self._lock:
            cached = self._cache.get(key)
            if cached and now < cached[1] - self.refresh_skew_seconds:
                return cached[0]
        secret = self.secret_provider.resolve(server.auth_env_ref)
        form = {
            "grant_type": "client_credentials",
            "client_id": server.oauth_client_id,
            "client_secret": secret,
            "scope": " ".join(server.oauth_scopes),
        }
        try:
            payload = await self._token_request(server.oauth_token_url, form, server.request_timeout_seconds)
        except McpCredentialError:
            raise
        except Exception as exc:
            raise McpCredentialError(
                "E_MCP_OAUTH_UPSTREAM",
                "OAuth Token Endpoint 暂时不可用",
                retryable=True,
            ) from exc
        raw_token = payload.get("access_token") if isinstance(payload, dict) else None
        raw_token_type = payload.get("token_type") if isinstance(payload, dict) else None
        raw_expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        token = raw_token.strip() if isinstance(raw_token, str) else ""
        token_type = raw_token_type.strip().casefold() if isinstance(raw_token_type, str) else ""
        try:
            expires_in = float(raw_expires_in) if not isinstance(raw_expires_in, bool) else 0.0
        except (TypeError, ValueError, OverflowError):
            expires_in = 0.0
        if (
            not token
            or len(token) > 16384
            or token_type != "bearer"
            or not math.isfinite(expires_in)
            or expires_in <= 0
        ):
            raise McpCredentialError("E_MCP_OAUTH_RESPONSE", "OAuth Token 响应字段无效")
        expires_at = now + min(expires_in, self.max_token_ttl_seconds)
        with self._lock:
            self._cache[key] = (token, expires_at)
        return token


__all__ = ["EnvironmentSecretProvider", "McpCredentialError", "McpCredentialService"]
