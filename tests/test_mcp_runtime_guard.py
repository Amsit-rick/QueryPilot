import asyncio

import pytest

from app.services.mcp_runtime_guard import McpGuardError, McpRuntimeGuard


def run(coro):
    return asyncio.run(coro)


def test_memory_guard_enforces_daily_call_budget():
    guard = McpRuntimeGuard(backend="memory", queue_timeout_seconds=0.01)

    async def scenario():
        async with guard.reserve(
            key="tenant-a:server-1",
            max_concurrency=2,
            daily_call_limit=1,
            consume_budget=True,
            failure_threshold=3,
            reset_seconds=30,
        ):
            pass
        with pytest.raises(McpGuardError, match="E_MCP_BUDGET"):
            async with guard.reserve(
                key="tenant-a:server-1",
                max_concurrency=2,
                daily_call_limit=1,
                consume_budget=True,
                failure_threshold=3,
                reset_seconds=30,
            ):
                pass

    run(scenario())


def test_memory_guard_limits_concurrency_without_leaking_slot():
    guard = McpRuntimeGuard(backend="memory", queue_timeout_seconds=0.01)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with guard.reserve(
                key="tenant-a:server-1",
                max_concurrency=1,
                daily_call_limit=10,
                consume_budget=False,
                failure_threshold=3,
                reset_seconds=30,
            ):
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await entered.wait()
        with pytest.raises(McpGuardError, match="E_MCP_CONCURRENCY"):
            async with guard.reserve(
                key="tenant-a:server-1",
                max_concurrency=1,
                daily_call_limit=10,
                consume_budget=False,
                failure_threshold=3,
                reset_seconds=30,
            ):
                pass
        release.set()
        await task
        async with guard.reserve(
            key="tenant-a:server-1",
            max_concurrency=1,
            daily_call_limit=10,
            consume_budget=False,
            failure_threshold=3,
            reset_seconds=30,
        ):
            pass

    run(scenario())


def test_memory_guard_opens_and_recovers_circuit():
    clock = [100.0]
    guard = McpRuntimeGuard(
        backend="memory",
        queue_timeout_seconds=0.01,
        monotonic=lambda: clock[0],
    )

    async def scenario():
        await guard.record_failure("tenant-a:server-1", failure_threshold=2, reset_seconds=30)
        await guard.record_failure("tenant-a:server-1", failure_threshold=2, reset_seconds=30)
        with pytest.raises(McpGuardError, match="E_MCP_CIRCUIT_OPEN"):
            async with guard.reserve(
                key="tenant-a:server-1",
                max_concurrency=1,
                daily_call_limit=10,
                consume_budget=False,
                failure_threshold=2,
                reset_seconds=30,
            ):
                pass
        clock[0] = 131.0
        async with guard.reserve(
            key="tenant-a:server-1",
            max_concurrency=1,
            daily_call_limit=10,
            consume_budget=False,
            failure_threshold=2,
            reset_seconds=30,
        ):
            pass
        await guard.record_success("tenant-a:server-1")

    run(scenario())


def test_redis_guard_fails_closed_when_backend_is_unavailable():
    def broken_factory(_url):
        raise OSError("redis unavailable with password=secret")

    guard = McpRuntimeGuard(
        backend="redis",
        redis_url="redis://shared",
        queue_timeout_seconds=0.01,
        redis_factory=broken_factory,
    )

    async def scenario():
        with pytest.raises(McpGuardError, match="E_MCP_GOVERNANCE_UNAVAILABLE") as exc:
            async with guard.reserve(
                key="tenant-a:server-1",
                max_concurrency=1,
                daily_call_limit=10,
                consume_budget=True,
                failure_threshold=2,
                reset_seconds=30,
            ):
                pass
        assert "secret" not in str(exc.value)

    run(scenario())


class FakeRedis:
    def __init__(self):
        self.calls = []

    def eval(self, script, key_count, *args):
        self.calls.append((script, key_count, args))
        if "ZREMRANGEBYSCORE" in script:
            return [1, 0, 0]
        return 1


def test_redis_guard_reserves_releases_and_records_circuit_state():
    redis = FakeRedis()
    guard = McpRuntimeGuard(
        backend="redis",
        redis_url="redis://shared",
        queue_timeout_seconds=0.01,
        redis_factory=lambda _url: redis,
        utc_date=lambda: "2026-11-03",
    )

    async def scenario():
        async with guard.reserve(
            key="tenant-a:server-1",
            max_concurrency=2,
            daily_call_limit=50,
            consume_budget=True,
            failure_threshold=3,
            reset_seconds=30,
            lease_seconds=12,
        ):
            pass
        await guard.record_failure("tenant-a:server-1", failure_threshold=3, reset_seconds=30)
        await guard.record_success("tenant-a:server-1")

    run(scenario())

    assert len(redis.calls) == 4
    reserve = redis.calls[0]
    release = redis.calls[1]
    failure = redis.calls[2]
    success = redis.calls[3]
    assert reserve[1] == 3
    assert reserve[2][0].endswith(":circuit")
    assert reserve[2][1].endswith(":concurrency")
    assert reserve[2][2].endswith(":budget:2026-11-03")
    assert reserve[2][-1] == 1
    assert release[1] == 1
    assert release[2][0].endswith(":concurrency")
    assert failure[2][-1] == "failure"
    assert success[2][-1] == "success"

