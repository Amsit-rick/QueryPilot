from contextlib import contextmanager


class _Connection:
    open = True

    def ping(self, reconnect=False):
        return True

    def close(self):
        self.open = False


def test_mysql_query_connection_uses_dedicated_readonly_identity(monkeypatch):
    from app.config import settings
    from app import mysql_db

    captured = {}
    monkeypatch.setattr(settings, "mysql_user", "control_writer")
    monkeypatch.setattr(settings, "mysql_password", "control-secret")
    monkeypatch.setattr(settings, "mysql_query_user", "business_reader")
    monkeypatch.setattr(settings, "mysql_query_password", "reader-secret")
    monkeypatch.setattr(mysql_db.pymysql, "connect", lambda **kwargs: captured.update(kwargs) or _Connection())

    with mysql_db.query_connection():
        pass

    assert captured["user"] == "business_reader"
    assert captured["password"] == "reader-secret"
    assert captured["init_command"] == "SET SESSION TRANSACTION READ ONLY"


def test_mysql_control_connection_keeps_control_plane_identity(monkeypatch):
    from app.config import settings
    from app import mysql_db

    captured = {}
    monkeypatch.setattr(settings, "mysql_user", "control_writer")
    monkeypatch.setattr(settings, "mysql_password", "control-secret")
    monkeypatch.setattr(settings, "mysql_query_user", "business_reader")
    monkeypatch.setattr(settings, "mysql_query_password", "reader-secret")
    monkeypatch.setattr(mysql_db.pymysql, "connect", lambda **kwargs: captured.update(kwargs) or _Connection())

    with mysql_db.connection():
        pass

    assert captured["user"] == "control_writer"
    assert captured["password"] == "control-secret"
    assert captured.get("init_command") != "SET SESSION TRANSACTION READ ONLY"


def test_postgres_query_connection_prefers_readonly_dsn(monkeypatch):
    from app.config import settings
    from app import postgres_db
    import psycopg

    captured = {}
    sentinel = object()
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql://control")
    monkeypatch.setattr(settings, "postgres_readonly_dsn", "postgresql://readonly")
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kwargs: captured.update({"dsn": dsn, **kwargs}) or sentinel)

    assert postgres_db._connect() is sentinel
    assert captured["dsn"] == "postgresql://readonly"
    assert "default_transaction_read_only=on" in captured["options"]
