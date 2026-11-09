from contextlib import contextmanager
from datetime import date, datetime, timezone

from app.repositories import mysql_proactive_analysis_repository as repository_module
from app.repositories.mysql_proactive_analysis_repository import MySQLProactiveAnalysisRepository


NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, *, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return tuple(self.rows)


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1


def fake_connect_queue(*connections):
    remaining = list(connections)

    @contextmanager
    def connect():
        yield remaining.pop(0)

    return connect


def plan_row(*, state="active", lease_token="lease-new"):
    return (
        "plan-1",
        "tenant-a",
        "owner-1",
        "检查销售异常",
        "sqlite-demo",
        60,
        6,
        0.5,
        1,
        state,
        NOW.replace(tzinfo=None),
        None,
        "",
        0,
        0.0,
        date(2026, 11, 8),
        lease_token,
        NOW.replace(tzinfo=None),
        NOW.replace(tzinfo=None),
        NOW.replace(tzinfo=None),
    )


def test_transition_keeps_owner_scope_and_expected_state_parameter_order(monkeypatch):
    update_cursor = FakeCursor(rowcount=1)
    read_cursor = FakeCursor(rows=[plan_row(state="paused", lease_token="")])
    monkeypatch.setattr(
        repository_module,
        "connect",
        fake_connect_queue(FakeConnection(update_cursor), FakeConnection(read_cursor)),
    )

    updated = MySQLProactiveAnalysisRepository().transition(
        "plan-1",
        tenant_id="tenant-a",
        owner_id="owner-1",
        expected_states={"active", "draft"},
        state="paused",
        next_run_at=None,
    )

    sql, params = update_cursor.calls[0]
    assert "tenant_id=%s AND owner_id=%s AND state IN (%s,%s)" in sql
    assert params == (
        "paused",
        None,
        "plan-1",
        "tenant-a",
        "owner-1",
        "active",
        "draft",
    )
    assert updated.state == "paused"


def test_complete_claim_requires_current_lease_token(monkeypatch):
    update_cursor = FakeCursor(rowcount=0)
    connection = FakeConnection(update_cursor)
    monkeypatch.setattr(repository_module, "connect", fake_connect_queue(connection))

    updated = MySQLProactiveAnalysisRepository().complete_claim(
        "plan-1",
        tenant_id="tenant-a",
        lease_token="lease-stale",
        run_at=NOW,
        next_run_at=NOW,
        task_id="task-1",
        cost_usd=0.02,
        count_run=True,
    )

    sql, params = update_cursor.calls[0]
    assert "WHERE id=%s AND tenant_id=%s AND lease_token=%s" in sql
    assert params[-3:] == ("plan-1", "tenant-a", "lease-stale")
    assert updated is None
