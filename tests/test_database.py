from app.database import database_status


def test_database_is_readonly_by_contract():
    status = database_status()
    assert status["readonly"] is True
    assert status["backend"] in {"sqlite", "mysql"}


def test_database_status_does_not_expose_local_path_or_connection_topology(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_backend", "sqlite")
    monkeypatch.setattr(settings, "database_path", "C:/private/tenant-a/secret.db")
    status = database_status()
    assert status["configured"] is True
    assert "path" not in status
    assert "secret.db" not in str(status)


def test_mysql_status_does_not_expose_host_or_database(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(settings, "mysql_host", "internal-db.example")
    monkeypatch.setattr(settings, "mysql_database", "customer_prod")
    monkeypatch.setattr("app.mysql_db.connect", lambda: (_ for _ in ()).throw(OSError("down")))
    status = database_status()
    assert status["backend"] == "mysql"
    assert "host" not in status and "database" not in status
    assert "internal-db.example" not in str(status)
    assert "customer_prod" not in str(status)


def test_postgres_alias_uses_postgres_health_probe(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_backend", "postgres")
    monkeypatch.setattr(settings, "postgres_dsn", "postgresql://internal/hidden")
    monkeypatch.setattr("app.postgres_db.test_postgres", lambda: {"ok": True})
    status = database_status()
    assert status["backend"] == "postgresql"
    assert status["connected"] is True
    assert "internal" not in str(status)
