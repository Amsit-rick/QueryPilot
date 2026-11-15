import sys
import types
from datetime import timezone

import pytest

from app.budget import build_usage_summary, cost_for_tokens, enforce_budget, estimate_query_cost, estimate_query_usage, estimate_tokens, settle_budget
from app.config import settings


def test_memory_budget_rejects_request_over_per_request_cap(monkeypatch):
    monkeypatch.setattr(settings, "budget_backend", "memory")
    monkeypatch.setattr(settings, "max_request_cost_usd", 0.08)
    with pytest.raises(ValueError, match="E_BUDGET_EXCEEDED"):
        enforce_budget(0.09)


def test_memory_budget_uses_utc_day_window_across_rotated_logs(monkeypatch):
    calls = []

    def fake_summarize(path, **kwargs):
        calls.append((path, kwargs))
        return {"total_cost_usd": 0.01}

    monkeypatch.setattr(settings, "budget_backend", "memory")
    monkeypatch.setattr(settings, "audit_log_path", "audit.jsonl")
    monkeypatch.setattr(settings, "audit_log_backup_count", 4)
    monkeypatch.setattr(settings, "daily_budget_usd", 1.0)
    monkeypatch.setattr("app.budget.summarize", fake_summarize)

    enforce_budget(0.01)

    assert calls[0][0] == "audit.jsonl"
    assert calls[0][1]["backup_count"] == 4
    since = calls[0][1]["since"]
    assert since.tzinfo == timezone.utc
    assert (since.hour, since.minute, since.second, since.microsecond) == (0, 0, 0, 0)


def test_query_cost_estimate_is_bounded_and_skips_explicit_sql(monkeypatch):
    monkeypatch.setattr(settings, "cost_per_1k_tokens_usd", 0.001)
    assert estimate_tokens("abcd") == 1
    assert estimate_query_cost("查询销售额") > 0
    assert estimate_query_cost("查询销售额", "SELECT 1") == 0
    assert estimate_query_usage("查询销售额") == (130, 512)
    assert estimate_query_usage("查询销售额", "SELECT 1") == (0, 0)
    assert cost_for_tokens(100, 200) == 0.0003


def test_usage_summary_prices_cache_hit_miss_and_output_separately(monkeypatch):
    monkeypatch.setattr(settings, "model_input_cache_hit_cny_per_million", 0.04)
    monkeypatch.setattr(settings, "model_input_cache_miss_cny_per_million", 2.0)
    monkeypatch.setattr(settings, "model_output_cny_per_million", 8.0)
    monkeypatch.setattr(settings, "model_pricing_basis", "configured-conservative")

    usage = build_usage_summary(
        input_tokens=531,
        output_tokens=64,
        cache_hit_input_tokens=100,
        cache_miss_input_tokens=400,
        usage_source="provider",
        model="deepseek-flash",
    )

    assert usage == {
        "input_tokens": 531,
        "output_tokens": 64,
        "total_tokens": 595,
        "cache_hit_input_tokens": 100,
        "cache_miss_input_tokens": 431,
        "cost_cny": 0.001378,
        "cost_cny_estimated": True,
        "usage_source": "provider",
        "model": "deepseek-flash",
        "pricing_basis": "configured-conservative",
    }


def test_usage_summary_treats_unclassified_input_as_cache_miss(monkeypatch):
    monkeypatch.setattr(settings, "model_input_cache_hit_cny_per_million", 0.04)
    monkeypatch.setattr(settings, "model_input_cache_miss_cny_per_million", 2.0)
    monkeypatch.setattr(settings, "model_output_cny_per_million", 8.0)

    usage = build_usage_summary(
        input_tokens=531,
        output_tokens=64,
        cache_hit_input_tokens=0,
        cache_miss_input_tokens=0,
        usage_source="estimate",
        model="deepseek-chat",
    )

    assert usage["cache_miss_input_tokens"] == 531
    assert usage["cost_cny"] == 0.001574
    assert usage["usage_source"] == "estimate"


def test_usage_summary_returns_zero_for_no_model_call_and_sanitizes_negatives():
    usage = build_usage_summary(
        input_tokens=-1,
        output_tokens=-2,
        cache_hit_input_tokens=-3,
        cache_miss_input_tokens=-4,
        usage_source="none",
        model="deepseek-chat",
    )

    assert usage["total_tokens"] == 0
    assert usage["cost_cny"] == 0.0
    assert usage["cost_cny_estimated"] is False
    assert usage["usage_source"] == "none"


def test_redis_budget_uses_atomic_reservation(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.results = iter(([1, 0.04], [0, 5.0]))

        def eval(self, script, keys, *args):
            self.calls.append((script, keys, args))
            return next(self.results)

    fake = FakeRedis()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: fake)),
    )
    monkeypatch.setattr(settings, "budget_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "redis://shared")
    monkeypatch.setattr(settings, "daily_budget_usd", 5.0)
    enforce_budget(0.04)
    with pytest.raises(ValueError, match="E_BUDGET_EXCEEDED"):
        enforce_budget(0.04)
    assert len(fake.calls) == 2
    assert "INCRBYFLOAT" in fake.calls[0][0]


def test_redis_budget_fails_closed_when_unavailable(monkeypatch):
    class BrokenRedis:
        def eval(self, *args):
            raise OSError("redis down")

    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: BrokenRedis())),
    )
    monkeypatch.setattr(settings, "budget_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "redis://shared")
    with pytest.raises(ValueError, match="E_BUDGET_UNAVAILABLE"):
        enforce_budget(0.01)


def test_redis_budget_settlement_reserves_only_positive_delta(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "budget_backend", "redis")
    monkeypatch.setattr("app.budget.enforce_budget", calls.append)
    settle_budget(0.04, 0.06)
    settle_budget(0.04, 0.03)
    assert calls == [pytest.approx(0.02), 0.0]


def test_memory_budget_settlement_checks_full_actual_cost(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "budget_backend", "memory")
    monkeypatch.setattr("app.budget.enforce_budget", calls.append)
    settle_budget(0.04, 0.06)
    assert calls == [0.06]
