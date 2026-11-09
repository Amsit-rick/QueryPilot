import sys
import types
from datetime import timezone

import pytest

from app.budget import cost_for_tokens, enforce_budget, estimate_query_cost, estimate_query_usage, estimate_tokens, settle_budget
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
