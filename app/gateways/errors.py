"""Stable, sanitized error taxonomy for optional database drivers."""

import re

from app.domain.data_gateway import GatewayError


_AUTH_CODES = {1017, 18456, 516}
_PERMISSION_CODES = {229, 1031, 164, 497}
_TIMEOUT_CODES = {12170, 159}
_CONNECTION_CODES = {12154, 12514, 12518, 12541, 12545, 20009, 20017, 209, 210}
_CANCELLED_CODES = {1013, 596, 394}


def _driver_code(error) -> int | None:
    values = [getattr(error, "code", None), *getattr(error, "args", ())]
    for value in values:
        value = getattr(value, "code", value)
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            continue
    match = re.search(r"(?:ORA-|error\s+|code\s*[:=]?\s*)(\d{3,6})", str(error), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def classify_gateway_error(error, *, operation: str, fallback_code: str = "E_GATEWAY_QUERY") -> GatewayError:
    """Map vendor exceptions to public codes without returning raw details."""
    code = _driver_code(error)
    name = type(error).__name__.lower()
    message = str(error).lower()
    if isinstance(error, TimeoutError) or code in _TIMEOUT_CODES or "timeout" in name or "timed out" in message:
        return GatewayError("E_GATEWAY_TIMEOUT", "Connector operation timed out", operation=operation, retryable=True)
    if code in _CANCELLED_CODES or "cancelled" in message or "canceled" in message:
        return GatewayError("E_GATEWAY_CANCELLED", "Connector operation was cancelled", operation=operation)
    if code in _AUTH_CODES or "login failed" in message or "invalid password" in message or "authentication failed" in message:
        return GatewayError("E_GATEWAY_AUTH", "Connector authentication failed", operation=operation)
    if code in _PERMISSION_CODES or "insufficient privilege" in message or "permission denied" in message or "access denied" in message:
        return GatewayError("E_GATEWAY_PERMISSION", "Connector permission denied", operation=operation)
    if (
        isinstance(error, ConnectionError)
        or code in _CONNECTION_CODES
        or "connection" in name
        or "network" in name
        or "no listener" in message
        or "connection refused" in message
    ):
        return GatewayError("E_GATEWAY_CONNECTION", "Connector connection failed", operation=operation, retryable=True)
    return GatewayError(fallback_code, "Connector operation failed", operation=operation)


__all__ = ["classify_gateway_error"]
