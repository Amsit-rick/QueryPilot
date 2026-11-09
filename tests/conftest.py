import pytest
from app.config import settings
from app.metrics_runtime import reset_metrics_for_tests
from app.mysql_db import reset_mysql_pools_for_tests
from app.rate_limit import _requests as local_rate_limit_requests


@pytest.fixture(autouse=True)
def local_test_identity(monkeypatch):
    # Tests exercise query behavior without depending on the production .env.
    # setup_local.cmd points a developer .env at mysql-main, so the datasource
    # default has to be pinned here as well or the suite changes behaviour on
    # an installed machine.
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(settings, "database_backend", "sqlite")
    monkeypatch.setattr(settings, "default_datasource", "sqlite-demo")
    reset_metrics_for_tests()
    reset_mysql_pools_for_tests()
    local_rate_limit_requests.clear()
    try:
        yield
    finally:
        local_rate_limit_requests.clear()
