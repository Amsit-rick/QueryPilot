from .config import settings


def database_status() -> dict:
    if settings.database_backend.lower() == "mysql":
        status = {"backend": "mysql", "configured": bool(settings.mysql_host and settings.mysql_database), "readonly": True, "connected": False}
        try:
            from .mysql_db import connect
            with connect() as conn: conn.ping(reconnect=False)
            status["connected"] = True
        except Exception:
            status["connected"] = False
        return status
    if settings.database_backend.lower() in {"postgres", "postgresql"}:
        configured = bool(settings.postgres_dsn)
        status = {"backend": "postgresql", "configured": configured, "readonly": True, "connected": False}
        if configured:
            try:
                from .postgres_db import test_postgres
                result = test_postgres()
                status["connected"] = bool(result.get("ok"))
            except Exception:
                pass
        return status
    return {"backend": "sqlite", "configured": True, "readonly": True, "connected": True}
