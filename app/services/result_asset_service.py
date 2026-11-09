"""Use cases for creating, authorizing and retiring result assets.

Every entry point assumes the caller already applied data-access policy and
redaction. This service never decides what a user may see; it only enforces
ownership, tenancy and expiry at the asset boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.domain.result_asset import (
    CSV,
    REPORT,
    SQL,
    TABLE,
    AccessContext,
    ResultAsset,
)
from app.repositories.result_asset_protocol import ResultAssetRepository
from app.services.result_preview_service import PreviewBudget, build_result_preview

DEFAULT_TTL = timedelta(days=7)
MAX_TTL = timedelta(days=90)


class ResultAssetService:
    """Create and guard result assets for one task pipeline."""

    def __init__(
        self,
        repository: ResultAssetRepository,
        *,
        default_ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.default_ttl = default_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(
        self,
        *,
        task_id: str,
        context: AccessContext,
        kind: str,
        name: str,
        uri: str = "",
        source_step_id: str = "",
        semantic_version: str = "",
        data_version: str = "",
        checksum: str = "",
        size_bytes: int = 0,
        metadata: dict[str, Any] | None = None,
        ttl: timedelta | None = None,
        expires_at: datetime | None = None,
    ) -> ResultAsset:
        """Create one asset with provenance and a bounded lifetime."""
        ttl = self.default_ttl if ttl is None else ttl
        if ttl > MAX_TTL:
            raise ValueError(f"E_ASSET_TTL:结果资产保留期不能超过 {MAX_TTL.days} 天")
        if ttl <= timedelta(0):
            raise ValueError("E_ASSET_TTL:结果资产保留期必须为正数")

        asset = ResultAsset(
            task_id=task_id,
            tenant_id=context.tenant_id,
            owner_id=context.user_id,
            kind=kind,
            name=name,
            uri=uri,
            source_step_id=source_step_id,
            semantic_version=semantic_version,
            data_version=data_version,
            checksum=checksum,
            size_bytes=size_bytes,
            metadata=dict(metadata or {}),
            expires_at=expires_at or (self._clock() + ttl),
        )
        return self.repository.save(asset)

    def create_table(
        self,
        *,
        task_id: str,
        context: AccessContext,
        columns: Sequence[str],
        rows: Sequence[Sequence[Any]],
        name: str = "查询结果表格",
        source_step_id: str = "",
        semantic_version: str = "",
        data_version: str = "",
        source_truncated: bool = False,
        budget: PreviewBudget | None = None,
        ttl: timedelta | None = None,
        expires_at: datetime | None = None,
    ) -> ResultAsset:
        """Build a table asset whose metadata is already JSON-safe.

        Delegates to RA-01 normalization so Decimal precision survives and
        non-finite values never reach a JSON parser.
        """
        preview = build_result_preview(
            columns,
            rows,
            budget=budget,
            source_truncated=source_truncated,
            extra_metadata={"data_scope": "redacted"},
        )
        return self.create(
            task_id=task_id,
            context=context,
            kind=TABLE,
            name=name,
            source_step_id=source_step_id,
            semantic_version=semantic_version,
            data_version=data_version,
            size_bytes=int(preview.get("byte_size", 0)),
            metadata=preview,
            ttl=ttl,
            expires_at=expires_at,
        )

    def create_sql(
        self,
        *,
        task_id: str,
        context: AccessContext,
        sql: str,
        dialect: str = "",
        name: str = "查询 SQL",
        **kwargs: Any,
    ) -> ResultAsset:
        if not sql.strip():
            raise ValueError("E_ASSET_SQL:SQL 资产内容不能为空")
        metadata = {"sql": sql.strip(), "dialect": dialect}
        return self.create(
            task_id=task_id, context=context, kind=SQL, name=name, metadata=metadata, **kwargs
        )

    def create_report(
        self,
        *,
        task_id: str,
        context: AccessContext,
        markdown: str,
        name: str = "分析报告",
        **kwargs: Any,
    ) -> ResultAsset:
        if not markdown.strip():
            raise ValueError("E_ASSET_REPORT:报告内容不能为空")
        metadata = {"markdown": markdown, "format": "markdown"}
        return self.create(
            task_id=task_id, context=context, kind=REPORT, name=name, metadata=metadata, **kwargs
        )

    def authorize_download(
        self, asset: ResultAsset | None, context: AccessContext
    ) -> ResultAsset:
        """Re-check authorization at download time, not only at list time.

        Listing an asset does not grant download rights. Tenancy, ownership and
        expiry are all re-evaluated here.
        """
        if asset is None:
            raise PermissionError("E_ASSET_NOT_FOUND:结果资产不存在或不属于当前租户")
        if asset.tenant_id != context.tenant_id:
            raise PermissionError("E_ASSET_FORBIDDEN:禁止跨租户访问结果资产")
        if not asset.belongs_to(context):
            raise PermissionError("E_ASSET_FORBIDDEN:禁止下载其他用户的结果资产")
        if asset.is_expired(self._clock()):
            raise PermissionError("E_ASSET_EXPIRED:结果资产已过期，请重新执行查询")
        return asset

    def list_for_task(self, task_id: str, context: AccessContext) -> list[ResultAsset]:
        """List task assets a user is allowed to see."""
        assets = self.repository.list_by_task(task_id, context.tenant_id)
        return [asset for asset in assets if asset.belongs_to(context)]

    def download(self, asset_id: str, context: AccessContext) -> ResultAsset:
        asset = self.repository.get(asset_id, context.tenant_id)
        return self.authorize_download(asset, context)

    def purge_expired(self, limit: int = 100) -> int:
        """Delete expired assets. Returns the number actually removed."""
        now = self._clock()
        removed = 0
        for asset in self.repository.list_expired(now, limit=limit):
            if self.repository.delete(asset.id, asset.tenant_id):
                removed += 1
        return removed

    def extend_expiry(self, asset_id: str, context: AccessContext, ttl: timedelta) -> ResultAsset:
        """Push expiry forward, preserving provenance and ownership."""
        asset = self.authorize_download(
            self.repository.get(asset_id, context.tenant_id), context
        )
        if ttl > MAX_TTL or ttl <= timedelta(0):
            raise ValueError("E_ASSET_TTL:结果资产保留期不合法")
        updated = replace(asset, expires_at=self._clock() + ttl)
        return self.repository.save(updated)

    @staticmethod
    def describe(asset: ResultAsset) -> dict[str, Any]:
        """Public projection; never exposes storage internals."""
        payload = asset.to_dict()
        payload["provenance"] = asset.provenance()
        return payload
