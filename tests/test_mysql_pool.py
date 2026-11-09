from __future__ import annotations

import threading
import time

import pytest

from app.mysql_db import _ConnectionPool


class FakeConnection:
    def __init__(self, name: str = "conn"):
        self.name = name
        self.open = True
        self.closed = False
        self.pings = 0
        self.rollbacks = 0

    def ping(self, reconnect: bool = False) -> bool:
        self.pings += 1
        return True

    def close(self) -> None:
        self.open = False
        self.closed = True

    def rollback(self) -> None:
        self.rollbacks += 1


class DeadConnection(FakeConnection):
    def ping(self, reconnect: bool = False) -> bool:
        raise RuntimeError("connection lost")


class RollbackFailureConnection(FakeConnection):
    def rollback(self) -> None:
        raise RuntimeError("rollback failed")


def _pool(factory, *, size=2, timeout=0.5, idle_ttl=300.0, clock=None, message="pool exhausted"):
    return _ConnectionPool(
        factory,
        size=size,
        timeout=timeout,
        idle_ttl=idle_ttl,
        exhausted_message=message,
        clock=clock or time.monotonic,
    )


def test_connections_are_reused_across_acquires():
    created = []

    def factory():
        conn = FakeConnection(str(len(created)))
        created.append(conn)
        return conn

    pool = _pool(factory)
    with pool.acquire() as first:
        pass
    with pool.acquire() as second:
        pass

    assert first is second
    assert len(created) == 1
    assert pool.idle_count == 1
    assert first.rollbacks == 2


def test_successful_release_rolls_back_before_connection_is_reused():
    pool = _pool(FakeConnection)

    with pool.acquire() as conn:
        assert conn.rollbacks == 0

    assert conn.rollbacks == 1
    with pool.acquire() as reused:
        assert reused is conn
        assert reused.rollbacks == 1

    assert conn.rollbacks == 2


def test_rollback_failure_drops_connection_instead_of_reusing_stale_state():
    created = []

    def factory():
        conn = RollbackFailureConnection() if not created else FakeConnection("replacement")
        created.append(conn)
        return conn

    pool = _pool(factory)
    with pool.acquire() as stale:
        pass

    assert stale.closed is True
    assert pool.idle_count == 0
    with pool.acquire() as replacement:
        pass
    assert replacement is not stale
    assert len(created) == 2


def test_dead_connection_is_replaced_on_checkout():
    created = []

    def factory():
        conn = DeadConnection() if not created else FakeConnection("good")
        created.append(conn)
        return conn

    pool = _pool(factory)
    with pool.acquire() as first:
        pass
    # A freshly created connection is not pinged; validation happens on the
    # next checkout, where the dead connection is closed and replaced.
    with pool.acquire() as second:
        pass
    assert first.closed is True
    assert second.open is True
    assert len(created) == 2


def test_connection_closed_by_caller_is_not_returned_to_the_pool():
    created = []

    def factory():
        conn = FakeConnection(str(len(created)))
        created.append(conn)
        return conn

    pool = _pool(factory)
    with pool.acquire() as conn:
        conn.close()

    assert pool.idle_count == 0
    with pool.acquire() as next_conn:
        pass
    assert len(created) == 2
    assert next_conn is not conn


def test_failure_inside_the_block_closes_the_connection_and_releases_the_slot():
    created = []

    def factory():
        conn = FakeConnection(str(len(created)))
        created.append(conn)
        return conn

    pool = _pool(factory, size=1)
    with pytest.raises(RuntimeError, match="boom"):
        with pool.acquire() as conn:
            raise RuntimeError("boom")

    assert conn.closed is True
    with pool.acquire() as replacement:
        pass
    assert len(created) == 2
    assert replacement is not conn


def test_idle_connection_is_replaced_after_the_ttl():
    now = [0.0]
    created = []

    def factory():
        conn = FakeConnection(str(len(created)))
        created.append(conn)
        return conn

    pool = _pool(factory, idle_ttl=10.0, clock=lambda: now[0])
    with pool.acquire():
        pass
    now[0] = 11.0
    with pool.acquire() as second:
        pass

    assert len(created) == 2
    assert second.open is True


def test_pool_exhaustion_times_out_with_a_stable_message():
    release = threading.Event()

    def factory():
        return FakeConnection()

    pool = _pool(factory, size=1, timeout=0.05, message="MySQL query connection limit reached")

    def hold():
        with pool.acquire():
            release.wait(timeout=2)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        with pytest.raises(TimeoutError, match="MySQL query connection limit reached"):
            with pool.acquire():
                pass
    finally:
        release.set()
        thread.join(timeout=2)


def test_close_all_drops_idle_connections():
    created = []

    def factory():
        conn = FakeConnection(str(len(created)))
        created.append(conn)
        return conn

    pool = _pool(factory)
    with pool.acquire():
        pass
    pool.close_all()

    assert pool.idle_count == 0
    assert created[0].closed is True
