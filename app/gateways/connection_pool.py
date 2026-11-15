"""Bounded connection pooling shared by remote DB-API gateways."""

from contextlib import contextmanager
import atexit
import threading
import time


class ConnectionPoolExhausted(TimeoutError):
    """Raised when no connection slot becomes available before the deadline."""


class RemoteConnectionPool:
    """Small thread-safe pool with rollback-on-return isolation.

    The pool deliberately avoids driver-specific liveness APIs. Idle
    connections are retired by TTL, while a connection that fails during a
    borrower operation is discarded instead of being returned to the pool.
    """

    def __init__(self, factory, *, size, timeout, idle_ttl, clock=time.monotonic):
        self._factory = factory
        self._timeout = max(0.0, float(timeout))
        self._idle_ttl = max(0.0, float(idle_ttl))
        self._clock = clock
        self._lock = threading.Lock()
        self._idle: list[tuple[object, float]] = []
        self._slots = threading.BoundedSemaphore(max(1, int(size)))

    @staticmethod
    def _close(connection) -> None:
        try:
            connection.close()
        except Exception:
            pass

    def _take(self):
        while True:
            with self._lock:
                entry = self._idle.pop() if self._idle else None
            if entry is None:
                return self._factory()
            connection, returned_at = entry
            if self._clock() - returned_at > self._idle_ttl:
                self._close(connection)
                continue
            return connection

    def _put(self, connection) -> None:
        try:
            # A reused connection must not leak a transaction, read snapshot,
            # session write, or failed transaction state to the next borrower.
            connection.rollback()
        except Exception:
            self._close(connection)
            return
        with self._lock:
            self._idle.append((connection, self._clock()))

    @contextmanager
    def acquire(self):
        if not self._slots.acquire(timeout=self._timeout):
            raise ConnectionPoolExhausted("connection pool exhausted")
        connection = None
        try:
            connection = self._take()
            yield connection
        except BaseException:
            if connection is not None:
                self._close(connection)
            raise
        else:
            self._put(connection)
        finally:
            self._slots.release()

    def close_all(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for connection, _ in idle:
            self._close(connection)

    @property
    def idle_count(self) -> int:
        with self._lock:
            return len(self._idle)


_shared_lock = threading.Lock()
_shared_pools: dict[tuple[object, ...], RemoteConnectionPool] = {}


def shared_remote_pool(key, factory, *, size, timeout, idle_ttl) -> RemoteConnectionPool:
    """Return the process-local pool for one stable connection identity."""
    normalized_key = (key, factory, int(size), float(timeout), float(idle_ttl))
    with _shared_lock:
        pool = _shared_pools.get(normalized_key)
        if pool is None:
            pool = RemoteConnectionPool(
                factory,
                size=size,
                timeout=timeout,
                idle_ttl=idle_ttl,
            )
            _shared_pools[normalized_key] = pool
        return pool


def close_shared_remote_pools() -> None:
    with _shared_lock:
        pools = list(_shared_pools.values())
        _shared_pools.clear()
    for pool in pools:
        pool.close_all()


atexit.register(close_shared_remote_pools)
