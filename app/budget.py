"""Daily request-cost budget with optional Redis coordination."""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil

from .audit import summarize
from .config import settings

_BUDGET_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local requested = tonumber(ARGV[1])
local daily = tonumber(ARGV[2])
if current + requested > daily then return {0, current} end
local total = redis.call('INCRBYFLOAT', KEYS[1], requested)
redis.call('EXPIRE', KEYS[1], 172800)
return {1, tonumber(total)}
"""

DEFAULT_ESTIMATED_OUTPUT_TOKENS = 512
ESTIMATED_SCHEMA_TOKENS = 128


def estimate_tokens(text: str) -> int:
    """Conservatively approximate tokens without claiming provider usage."""
    normalized = str(text or "")
    return ceil(len(normalized) / 4) if normalized else 0


def estimate_query_cost(question: str, sql: str = "") -> float:
    """Estimate one model-backed query cost for budget preflight.

    Explicit SQL does not invoke the model and therefore reserves no model
    budget. For natural-language generation, include a small schema/context
    allowance and a bounded output allowance. Provider usage should replace
    this estimate when a real model integration exposes usage metadata.
    """
    input_tokens, output_tokens = estimate_query_usage(question, sql)
    return round(((input_tokens + output_tokens) / 1000) * settings.cost_per_1k_tokens_usd, 8)


def estimate_query_usage(question: str, sql: str = "") -> tuple[int, int]:
    """Return estimated input/output tokens for one model-backed request."""
    if str(sql or "").strip():
        return 0, 0
    return estimate_tokens(question) + ESTIMATED_SCHEMA_TOKENS, DEFAULT_ESTIMATED_OUTPUT_TOKENS


def cost_for_tokens(input_tokens: int, output_tokens: int) -> float:
    """Calculate cost from provider-reported or estimated token counts."""
    return round((max(0, int(input_tokens)) + max(0, int(output_tokens))) / 1000 * settings.cost_per_1k_tokens_usd, 8)


def _enforce_redis(estimated_cost: float, redis_url: str) -> None:
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        key = f"query-agent:budget:{datetime.now(timezone.utc):%Y-%m-%d}"
        result = client.eval(
            _BUDGET_LUA,
            1,
            key,
            f"{estimated_cost:.12f}",
            f"{settings.daily_budget_usd:.12f}",
        )
        if int(result[0]) == 0:
            raise ValueError("E_BUDGET_EXCEEDED:预算超限")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("E_BUDGET_UNAVAILABLE:共享预算服务暂时不可用") from exc


def enforce_budget(estimated_cost: float = 0.0) -> None:
    """Reserve estimated cost atomically when Redis budget mode is enabled."""
    estimated_cost = max(0.0, float(estimated_cost))
    if estimated_cost > settings.max_request_cost_usd:
        raise ValueError("E_BUDGET_EXCEEDED:预算超限")
    if settings.budget_backend == "redis":
        if not settings.redis_url.strip():
            raise ValueError("E_BUDGET_UNAVAILABLE:共享预算服务暂时不可用")
        _enforce_redis(estimated_cost, settings.redis_url)
        return
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if summarize(
        settings.audit_log_path,
        backup_count=settings.audit_log_backup_count,
        since=day_start,
    )["total_cost_usd"] + estimated_cost > settings.daily_budget_usd:
        raise ValueError("E_BUDGET_EXCEEDED:预算超限")


def settle_budget(estimated_cost: float, actual_cost: float) -> None:
    """Reconcile provider usage before database execution.

    Redis already reserved the estimate, so only a positive delta is added.
    Memory mode does not persist reservations and therefore checks the full
    actual cost against the append-only audit total.
    """
    estimated = max(0.0, float(estimated_cost))
    actual = max(0.0, float(actual_cost))
    if settings.budget_backend == "redis":
        enforce_budget(max(0.0, actual - estimated))
    else:
        enforce_budget(actual)
