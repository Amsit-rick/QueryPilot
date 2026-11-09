import sys
import types

import pytest
from fastapi import HTTPException

from app.backup import backup_database
from app.db import init_db
from app.rate_limit import enforce_rate_limit
from app.recovery import restore_database
from scripts.load_enterprise_mysql import required_env


def test_rate_limit_rejects_excessive_requests():
    key = "test-rate-limit"
    for _ in range(2):
        enforce_rate_limit(key, limit=2)
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limit(key, limit=2)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) > 0


def test_rate_limit_window_expires(monkeypatch):
    import app.rate_limit as rate_limit

    key = "test-rate-window"
    clock = iter([0.0, 0.1, 61.0])
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: next(clock))
    enforce_rate_limit(key, limit=1)
    with pytest.raises(HTTPException):
        enforce_rate_limit(key, limit=1)
    enforce_rate_limit(key, limit=1)


def test_redis_rate_limit_uses_atomic_shared_script(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.results = iter(([1, 0], [0, 2500]))

        def eval(self, script, keys, *args):
            self.calls.append((script, keys, args))
            return next(self.results)

    fake = FakeRedis()
    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: fake)),
    )
    enforce_rate_limit("shared-user", limit=1, redis_url="redis://shared")
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limit("shared-user", limit=1, redis_url="redis://shared")
    assert exc.value.status_code == 429
    assert len(fake.calls) == 2
    assert "ZREMRANGEBYSCORE" in fake.calls[0][0]


def test_redis_rate_limit_fails_closed_when_unavailable(monkeypatch):
    class BrokenRedis:
        def eval(self, *args):
            raise OSError("redis down")

    monkeypatch.setitem(
        sys.modules,
        "redis",
        types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: BrokenRedis())),
    )
    with pytest.raises(HTTPException) as exc:
        enforce_rate_limit("unavailable-user", redis_url="redis://shared")
    assert exc.value.status_code == 503


def test_restore_creates_target(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    init_db(str(source))
    backup = backup_database(str(source), str(tmp_path / "backups"))
    assert restore_database(backup, str(target)) == str(target)


def test_enterprise_loader_requires_explicit_password(monkeypatch):
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="MYSQL_PASSWORD must be set"):
        required_env("MYSQL_PASSWORD")
