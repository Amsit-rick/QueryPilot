from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.gateways.connection_pool import RemoteConnectionPool
from app.gateways.oracle_gateway import OracleGateway
from app.gateways.sqlserver_gateway import SQLServerGateway
from app.gateways.clickhouse_gateway import ClickHouseGateway


class Cursor:
    description = [("value",)]

    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.closed = False

    def execute(self, sql, params=None):
        self.rows = [(1,)]

    def fetchone(self):
        return self.rows.pop(0)

    def close(self):
        self.closed = True


class Connection:
    def __init__(self):
        self.closed = False
        self.rollbacks = 0

    def cursor(self):
        return Cursor(self)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_remote_pool_reuses_connection_rolls_back_and_closes_idle_connections():
    connections = []

    def factory():
        connection = Connection()
        connections.append(connection)
        return connection

    pool = RemoteConnectionPool(factory, size=1, timeout=0.1, idle_ttl=60)
    with pool.acquire() as first:
        assert first is connections[0]
    with pool.acquire() as second:
        assert second is connections[0]

    assert len(connections) == 1
    assert connections[0].rollbacks == 2
    assert connections[0].closed is False
    pool.close_all()
    assert connections[0].closed is True


def test_remote_pool_times_out_when_all_slots_are_borrowed():
    pool = RemoteConnectionPool(Connection, size=1, timeout=0.05, idle_ttl=60)
    entered = Event()
    release = Event()

    def hold_connection():
        with pool.acquire():
            entered.set()
            release.wait(2)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(hold_connection)
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="connection pool exhausted"):
            with pool.acquire():
                pass
        release.set()
        future.result(timeout=2)


def test_remote_pool_discards_connection_after_borrower_error():
    connections = []

    def factory():
        connection = Connection()
        connections.append(connection)
        return connection

    pool = RemoteConnectionPool(factory, size=1, timeout=0.1, idle_ttl=60)
    with pytest.raises(RuntimeError):
        with pool.acquire():
            raise RuntimeError("query failed")
    assert connections[0].closed is True

    with pool.acquire() as replacement:
        assert replacement is connections[1]


def test_remote_pool_retires_connection_after_idle_ttl():
    now = [10.0]
    connections = []

    def factory():
        connection = Connection()
        connections.append(connection)
        return connection

    pool = RemoteConnectionPool(factory, size=1, timeout=0.1, idle_ttl=5, clock=lambda: now[0])
    with pool.acquire():
        pass
    now[0] = 16.0

    with pool.acquire() as replacement:
        assert replacement is connections[1]

    assert connections[0].closed is True


@pytest.mark.parametrize("gateway_type", [OracleGateway, SQLServerGateway, ClickHouseGateway])
def test_remote_gateway_uses_pool_for_default_connections(monkeypatch, gateway_type):
    connections = []

    def factory():
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(gateway_type, "default_connect", staticmethod(factory))
    gateway = gateway_type(gateway_id="remote", name="Remote")

    assert gateway.health().status == "healthy"
    assert gateway.health().status == "healthy"
    assert len(connections) == 1
    assert connections[0].rollbacks == 2

    gateway.close()
    assert connections[0].closed is True


@pytest.mark.parametrize("gateway_type", [OracleGateway, SQLServerGateway, ClickHouseGateway])
def test_default_gateway_instances_share_process_pool(monkeypatch, gateway_type):
    connections = []

    def factory():
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(gateway_type, "default_connect", staticmethod(factory))
    first = gateway_type(gateway_id="first", name="First")
    second = gateway_type(gateway_id="second", name="Second")

    assert first.health().status == "healthy"
    assert second.health().status == "healthy"
    assert len(connections) == 1

    first.close()
