from datetime import datetime, timezone

from app.domain.skill import SkillDefinition, SkillVersion
from app.repositories import mysql_skill_repository as repository_module
from app.repositories.mysql_skill_repository import MySQLSkillRepository


class _Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _record():
    definition = SkillDefinition(
        id="sales-analysis",
        version="1.1.0",
        name="销售分析",
        description="租户版本",
        intents=("sales_query",),
        trigger_terms=("销售",),
        allowed_tools=("schema.list", "query.plan"),
        workflow_steps=("确认口径",),
    )
    return SkillVersion(
        tenant_id="tenant-a",
        definition=definition,
        state="draft",
        created_by="admin-1",
    )


def test_mysql_skill_repository_serializes_declaration_and_uses_tenant_scope(monkeypatch):
    cursor = _Cursor()
    connection = _Connection(cursor)
    monkeypatch.setattr(repository_module, "connect", lambda: connection)

    MySQLSkillRepository().create(_record())

    sql, params = cursor.executed[0]
    assert "INSERT INTO skill_versions" in sql
    assert params[1] == "tenant-a"
    assert '"allowed_tools"' in params[4]
    assert "system_prompt" not in params[4]
    assert connection.commits == 1


def test_mysql_skill_repository_activate_is_single_transaction(monkeypatch):
    record = _record()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = (
        record.id,
        "tenant-a",
        "sales-analysis",
        "1.1.0",
        record.definition.model_dump_json(),
        "active",
        0,
        "admin-1",
        now,
        now,
    )
    cursor = _Cursor([row])
    connection = _Connection(cursor)
    monkeypatch.setattr(repository_module, "connect", lambda: connection)

    activated = MySQLSkillRepository().activate(record.id, "tenant-a")

    statements = [item[0] for item in cursor.executed]
    assert any("FOR UPDATE" in sql for sql in statements)
    assert any("state='retired'" in sql for sql in statements)
    assert any("state='active'" in sql for sql in statements)
    assert activated.state == "active"
    assert connection.commits == 1
