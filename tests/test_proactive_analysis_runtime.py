from types import SimpleNamespace

import pytest

from app.config import settings
from app.domain.proactive_analysis import ProactiveAnalysisSpec
from app.proactive_analysis_runtime import _resolve_identity, get_proactive_analysis_runner


def _plan(owner_id="1", tenant_id="tenant-a"):
    return SimpleNamespace(
        owner_id=owner_id,
        tenant_id=tenant_id,
        spec=ProactiveAnalysisSpec(question="检查销售异常", datasource_id="sqlite-demo"),
    )


def test_runner_factory_fails_closed_when_feature_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "proactive_analysis_enabled", False)

    with pytest.raises(RuntimeError, match="E_PROACTIVE_DISABLED"):
        get_proactive_analysis_runner()


def test_identity_resolver_supports_only_local_dev_when_auth_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)

    assert _resolve_identity(_plan(owner_id="local-dev")) == {
        "subject": "local-dev",
        "tenant_id": "tenant-a",
        "roles": ["analyst"],
    }
    with pytest.raises(RuntimeError, match="E_PROACTIVE_IDENTITY_UNSUPPORTED"):
        _resolve_identity(_plan(owner_id="api-key-subject"))


def test_identity_resolver_rejects_revoked_and_cross_tenant_users(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(
        "app.proactive_analysis_runtime.get_user_by_id",
        lambda _user_id: {"id": 1, "tenant_id": "tenant-a", "role": "analyst", "is_active": False},
    )
    with pytest.raises(RuntimeError, match="E_PROACTIVE_IDENTITY_REVOKED"):
        _resolve_identity(_plan())

    monkeypatch.setattr(
        "app.proactive_analysis_runtime.get_user_by_id",
        lambda _user_id: {"id": 1, "tenant_id": "tenant-b", "role": "analyst", "is_active": True},
    )
    with pytest.raises(RuntimeError, match="E_PROACTIVE_IDENTITY_TENANT"):
        _resolve_identity(_plan())
