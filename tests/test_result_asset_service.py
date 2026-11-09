"""Unit tests for the result asset contract. No database required."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.result_asset import (
    CHART,
    CSV,
    REPORT,
    SQL,
    TABLE,
    AccessContext,
    ResultAsset,
)
from app.repositories.result_asset_protocol import InMemoryResultAssetRepository
from app.services.result_asset_service import MAX_TTL, ResultAssetService

TENANT = "tenant-a"
OWNER = "user-1"
OTHER_USER = "user-2"
OTHER_TENANT = "tenant-b"

FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def make_service(**kwargs):
    return ResultAssetService(
        InMemoryResultAssetRepository(), clock=lambda: FIXED_NOW, **kwargs
    )


def owner_context(roles=()):
    return AccessContext(tenant_id=TENANT, user_id=OWNER, roles=tuple(roles))


# --- contract validation ---


def test_all_declared_kinds_are_accepted():
    for kind in (TABLE, SQL, CSV, CHART, REPORT):
        asset = ResultAsset(
            task_id="t1", tenant_id=TENANT, owner_id=OWNER, kind=kind, name="asset"
        )
        assert asset.kind == kind


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="E_ASSET_KIND"):
        ResultAsset(
            task_id="t1", tenant_id=TENANT, owner_id=OWNER, kind="excel", name="asset"
        )


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"name": "  "}, "E_ASSET_NAME"),
        ({"tenant_id": ""}, "E_ASSET_TENANT"),
        ({"owner_id": ""}, "E_ASSET_OWNER"),
        ({"size_bytes": -1}, "E_ASSET_SIZE"),
    ],
)
def test_domain_invariants(payload, expected):
    base = {"task_id": "t1", "tenant_id": TENANT, "owner_id": OWNER, "kind": TABLE, "name": "a"}
    with pytest.raises(ValueError, match=expected):
        ResultAsset(**{**base, **payload})


def test_name_is_trimmed():
    asset = ResultAsset(
        task_id="t1", tenant_id=TENANT, owner_id=OWNER, kind=TABLE, name="  销售额  "
    )
    assert asset.name == "销售额"


# --- provenance and expiry ---


def test_provenance_captures_step_and_versions():
    service = make_service()
    asset = service.create(
        task_id="t1",
        context=owner_context(),
        kind=TABLE,
        name="表格",
        source_step_id="step-7",
        semantic_version="sem-2026.09",
        data_version="sales@2026-09-12",
    )
    assert asset.provenance() == {
        "source_step_id": "step-7",
        "semantic_version": "sem-2026.09",
        "data_version": "sales@2026-09-12",
    }


def test_expiry_uses_default_ttl():
    service = make_service()
    asset = service.create(
        task_id="t1", context=owner_context(), kind=TABLE, name="表格", ttl=timedelta(days=3)
    )
    assert asset.expires_at == FIXED_NOW + timedelta(days=3)


def test_ttl_above_max_is_rejected():
    service = make_service()
    with pytest.raises(ValueError, match="E_ASSET_TTL"):
        service.create(
            task_id="t1",
            context=owner_context(),
            kind=TABLE,
            name="表格",
            ttl=MAX_TTL + timedelta(days=1),
        )


def test_non_positive_ttl_is_rejected():
    service = make_service()
    with pytest.raises(ValueError, match="E_ASSET_TTL"):
        service.create(
            task_id="t1", context=owner_context(), kind=TABLE, name="表格", ttl=timedelta(0)
        )


def test_asset_without_expiry_never_expires():
    asset = ResultAsset(
        task_id="t1",
        tenant_id=TENANT,
        owner_id=OWNER,
        kind=TABLE,
        name="永久",
        expires_at=None,
    )
    assert asset.is_expired(FIXED_NOW + timedelta(days=3650)) is False


def test_expiry_boundary_is_inclusive():
    asset = ResultAsset(
        task_id="t1",
        tenant_id=TENANT,
        owner_id=OWNER,
        kind=TABLE,
        name="表格",
        expires_at=FIXED_NOW,
    )
    assert asset.is_expired(FIXED_NOW) is True
    assert asset.is_expired(FIXED_NOW - timedelta(seconds=1)) is False


# --- download re-authorization ---


def test_owner_can_download():
    service = make_service()
    asset = service.create(task_id="t1", context=owner_context(), kind=TABLE, name="表格")
    assert service.download(asset.id, owner_context()) is asset


def test_cross_user_download_is_denied():
    service = make_service()
    asset = service.create(task_id="t1", context=owner_context(), kind=TABLE, name="表格")
    stranger = AccessContext(tenant_id=TENANT, user_id=OTHER_USER)
    with pytest.raises(PermissionError, match="E_ASSET_FORBIDDEN"):
        service.download(asset.id, stranger)


def test_admin_may_reach_other_users_assets_in_same_tenant():
    service = make_service()
    asset = service.create(task_id="t1", context=owner_context(), kind=TABLE, name="表格")
    admin = AccessContext(tenant_id=TENANT, user_id=OTHER_USER, roles=("admin",))
    assert service.download(asset.id, admin) is asset


def test_cross_tenant_download_is_denied_even_for_admin():
    service = make_service()
    asset = service.create(task_id="t1", context=owner_context(), kind=TABLE, name="表格")
    outsider = AccessContext(tenant_id=OTHER_TENANT, user_id=OWNER, roles=("admin",))
    with pytest.raises(PermissionError, match="禁止跨租户|E_ASSET_NOT_FOUND"):
        service.download(asset.id, outsider)


def test_expired_asset_cannot_be_downloaded():
    service = make_service()
    asset = service.create(
        task_id="t1",
        context=owner_context(),
        kind=TABLE,
        name="表格",
        expires_at=FIXED_NOW - timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="E_ASSET_EXPIRED"):
        service.download(asset.id, owner_context())


def test_missing_asset_is_not_leaked_as_500():
    service = make_service()
    with pytest.raises(PermissionError, match="E_ASSET_NOT_FOUND"):
        service.download("does-not-exist", owner_context())


def test_listing_does_not_grant_download_rights():
    service = make_service()
    service.create(task_id="t1", context=owner_context(), kind=TABLE, name="表格")
    listed = service.list_for_task("t1", owner_context())
    assert len(listed) == 1
    # Listing succeeded, but a different user still cannot download it.
    with pytest.raises(PermissionError):
        service.download(listed[0].id, AccessContext(tenant_id=TENANT, user_id=OTHER_USER))


def test_list_for_task_filters_other_users_assets():
    service = make_service()
    service.create(task_id="t1", context=owner_context(), kind=TABLE, name="我的")
    service.create(
        task_id="t1",
        context=AccessContext(tenant_id=TENANT, user_id=OTHER_USER),
        kind=TABLE,
        name="他人的",
    )
    assert len(service.list_for_task("t1", owner_context())) == 1


# --- retention ---


def test_purge_expired_removes_only_expired_assets():
    service = make_service()
    service.create(
        task_id="t1",
        context=owner_context(),
        kind=TABLE,
        name="过期的",
        expires_at=FIXED_NOW - timedelta(seconds=1),
    )
    fresh = service.create(
        task_id="t1", context=owner_context(), kind=TABLE, name="新鲜的", ttl=timedelta(days=1)
    )
    removed = service.purge_expired()
    assert removed == 1
    assert service.download(fresh.id, owner_context()) is fresh


def test_extend_expiry_preserves_ownership():
    service = make_service()
    asset = service.create(task_id="t1", context=owner_context(), kind=TABLE, name="表格")
    extended = service.extend_expiry(asset.id, owner_context(), timedelta(days=30))
    assert extended.owner_id == OWNER
    assert extended.expires_at == FIXED_NOW + timedelta(days=30)


# --- integration with RA-01 normalization ---


def test_table_asset_metadata_is_json_safe():
    import json

    service = make_service()
    asset = service.create_table(
        task_id="t1",
        context=owner_context(),
        columns=["amount", "ratio"],
        rows=[[Decimal("1.10"), float("nan")]],
        semantic_version="sem-1",
    )
    json.dumps(asset.to_dict(), allow_nan=False)
    assert asset.metadata["rows"] == [["1.10", None]]
    assert asset.metadata["data_scope"] == "redacted"
    assert asset.size_bytes > 0


def test_table_asset_records_truncation_reasons():
    service = make_service()
    asset = service.create_table(
        task_id="t1",
        context=owner_context(),
        columns=["a"],
        rows=[[index] for index in range(250)],
        source_truncated=True,
    )
    assert asset.metadata["preview_count"] == 100
    assert "row_limit" in asset.metadata["truncation_reasons"]
    assert "source_truncated" in asset.metadata["truncation_reasons"]


def test_sql_and_report_assets_reject_empty_content():
    service = make_service()
    with pytest.raises(ValueError, match="E_ASSET_SQL"):
        service.create_sql(task_id="t1", context=owner_context(), sql="   ")
    with pytest.raises(ValueError, match="E_ASSET_REPORT"):
        service.create_report(task_id="t1", context=owner_context(), markdown="")


def test_describe_exposes_provenance_without_storage_internals():
    service = make_service()
    asset = service.create(
        task_id="t1",
        context=owner_context(),
        kind=CSV,
        name="导出",
        semantic_version="sem-2",
    )
    payload = ResultAssetService.describe(asset)
    assert payload["provenance"]["semantic_version"] == "sem-2"
    assert payload["kind"] == CSV
