import pytest

from app.services.datasource_service import DataSourceAccessService


def _service():
    sources = {"demo": {"id": "demo", "roles": ["analyst"]}}
    return DataSourceAccessService(
        get_datasource=sources.get,
        can_use=lambda identity, ds: bool(set(identity.get("roles", [])) & set(ds["roles"])),
        schema=lambda ds: {"sales": {"id"}},
        execute=lambda ds, sql, params, max_rows, offset: (ds["id"], sql, params, max_rows, offset),
    )


def test_datasource_service_resolves_authorized_source():
    service = _service()
    source, current_schema = service.load_schema({"roles": ["analyst"]}, "demo")
    assert source["id"] == "demo"
    assert current_schema == {"sales": {"id"}}


def test_datasource_service_rejects_unknown_or_forbidden_source():
    service = _service()
    with pytest.raises(LookupError, match="数据源不存在"):
        service.resolve({"roles": ["analyst"]}, "missing")
    with pytest.raises(PermissionError, match="无权访问"):
        service.resolve({"roles": ["viewer"]}, "demo")
