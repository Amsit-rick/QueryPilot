"""Persistence protocol for result assets.

Concrete database implementations live in infrastructure. Services depend only
on this protocol so they stay testable without a database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.result_asset import ResultAsset


class ResultAssetRepository(Protocol):
    def save(self, asset: ResultAsset) -> ResultAsset:
        """Insert or update an asset keyed by its id."""
        ...

    def get(self, asset_id: str, tenant_id: str) -> ResultAsset | None:
        """Fetch an asset, returning None when it belongs to another tenant."""
        ...

    def list_by_task(self, task_id: str, tenant_id: str) -> list[ResultAsset]:
        """List every asset attached to a task within one tenant."""
        ...

    def delete(self, asset_id: str, tenant_id: str) -> bool:
        """Delete an asset, returning whether a row was removed."""
        ...

    def list_expired(self, now: datetime, limit: int = 100) -> list[ResultAsset]:
        """List expired assets across all tenants for retention cleanup."""
        ...


class InMemoryResultAssetRepository:
    """Test double. Not a production implementation."""

    def __init__(self) -> None:
        self._rows: dict[str, ResultAsset] = {}

    def save(self, asset: ResultAsset) -> ResultAsset:
        self._rows[asset.id] = asset
        return asset

    def get(self, asset_id: str, tenant_id: str) -> ResultAsset | None:
        asset = self._rows.get(asset_id)
        if asset is None or asset.tenant_id != tenant_id:
            return None
        return asset

    def list_by_task(self, task_id: str, tenant_id: str) -> list[ResultAsset]:
        return [
            asset
            for asset in self._rows.values()
            if asset.task_id == task_id and asset.tenant_id == tenant_id
        ]

    def delete(self, asset_id: str, tenant_id: str) -> bool:
        asset = self._rows.get(asset_id)
        if asset is None or asset.tenant_id != tenant_id:
            return False
        del self._rows[asset_id]
        return True

    def list_expired(self, now: datetime, limit: int = 100) -> list[ResultAsset]:
        expired = [asset for asset in self._rows.values() if asset.is_expired(now)]
        return expired[:limit]
