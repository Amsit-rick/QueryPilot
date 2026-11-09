import sys
import types

import pytest

from app import circuit_breaker


def test_memory_circuit_opens_after_threshold(monkeypatch):
    circuit_breaker.reset_memory()
    circuit_breaker.record_failure(key="model", threshold=2, reset_seconds=30)
    circuit_breaker.allow_request(key="model", threshold=2, reset_seconds=30)
    circuit_breaker.record_failure(key="model", threshold=2, reset_seconds=30)
    with pytest.raises(RuntimeError, match="熔断"):
        circuit_breaker.allow_request(key="model", threshold=2, reset_seconds=30)
    circuit_breaker.record_success(key="model", threshold=2, reset_seconds=30)
    circuit_breaker.allow_request(key="model", threshold=2, reset_seconds=30)


def test_memory_circuit_is_isolated_by_provider_key():
    circuit_breaker.reset_memory()
    circuit_breaker.record_failure(key="provider-a", threshold=1, reset_seconds=30)

    with pytest.raises(RuntimeError, match="熔断"):
        circuit_breaker.allow_request(key="provider-a", threshold=1, reset_seconds=30)

    circuit_breaker.allow_request(key="provider-b", threshold=1, reset_seconds=30)


def test_redis_circuit_uses_atomic_state_script(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.results = iter(([1, 0], [1, 0], [0, 30], [1, 0]))

        def eval(self, script, keys, *args):
            self.calls.append((script, keys, args))
            return next(self.results)

    fake = FakeRedis()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: fake)),
    )
    kwargs = {"key": "model", "threshold": 2, "reset_seconds": 30, "redis_url": "redis://shared"}
    circuit_breaker.allow_request(**kwargs)
    circuit_breaker.record_failure(**kwargs)
    with pytest.raises(RuntimeError, match="熔断"):
        circuit_breaker.allow_request(**kwargs)
    circuit_breaker.record_success(**kwargs)
    assert len(fake.calls) == 4
    assert "HGET" in fake.calls[0][0]
    assert fake.calls[1][2][-1] == "failure"


def test_redis_circuit_fails_closed_when_unavailable(monkeypatch):
    class BrokenRedis:
        def eval(self, *args):
            raise OSError("redis down")

    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: BrokenRedis())),
    )
    with pytest.raises(circuit_breaker.CircuitBackendUnavailable):
        circuit_breaker.allow_request(key="model", threshold=2, reset_seconds=30, redis_url="redis://shared")
