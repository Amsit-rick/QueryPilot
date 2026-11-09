def test_backend_setting_supports_mysql(monkeypatch):
    from app.config import settings
    from app import main as main_module
    monkeypatch.setattr(settings, "database_backend", "mysql")
    monkeypatch.setattr(main_module, "mysql_schema", lambda: {"sales": {"id"}, "customers": {"id"}})
    from app.main import get_schema
    result = get_schema()
    assert "sales" in result and "customers" in result
