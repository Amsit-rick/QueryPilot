"""Safe reuse of previously authorized result assets in follow-up questions.

The risk this service exists to contain: a follow-up question may try to pull
historical result data into a model prompt. Doing that without re-checking
tenancy, ownership and expiry would resurrect data the caller may no longer be
allowed to see.

Every reference therefore goes through the full download authorization path
before a single value is returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.result_asset import SQL, TABLE, AccessContext
from app.services.result_asset_service import ResultAssetService

DEFAULT_MAX_ROWS = 50
DEFAULT_MAX_COLUMNS = 12
MAX_CONTEXT_BYTES = 8192


@dataclass(frozen=True)
class AssetReference:
    """The smallest slice of an asset that a follow-up question may use."""

    asset_id: str
    task_id: str
    kind: str
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    total_rows: int = 0
    total_columns: int = 0
    truncated: bool = False
    semantic_version: str = ""
    data_version: str = ""
    referenced_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "total_rows": self.total_rows,
            "total_columns": self.total_columns,
            "truncated": self.truncated,
            "semantic_version": self.semantic_version,
            "data_version": self.data_version,
            "referenced_at": self.referenced_at,
        }


class ArtifactReferenceService:
    """Resolve and bound references to prior authorized assets."""

    def __init__(
        self,
        asset_service: ResultAssetService,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_columns: int = DEFAULT_MAX_COLUMNS,
        max_context_bytes: int = MAX_CONTEXT_BYTES,
    ) -> None:
        self.asset_service = asset_service
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.max_context_bytes = max_context_bytes

    def resolve(
        self,
        asset_id: str,
        context: AccessContext,
        *,
        columns: list[str] | None = None,
        max_rows: int | None = None,
    ) -> AssetReference:
        """Authorize first, then return only the requested, bounded slice."""
        # Authorization is mandatory: tenant, owner and expiry are re-checked.
        asset = self.asset_service.download(asset_id, context)

        now = datetime.now(timezone.utc).isoformat()
        metadata = asset.metadata or {}

        if asset.kind == SQL:
            return AssetReference(
                asset_id=asset.id,
                task_id=asset.task_id,
                kind=SQL,
                total_rows=0,
                total_columns=0,
                semantic_version=asset.semantic_version,
                data_version=asset.data_version,
                referenced_at=now,
            )

        if asset.kind != TABLE:
            raise ValueError(f"E_REFERENCE_KIND:暂不支持引用 {asset.kind!r} 类型的结果资产")

        available = [str(name) for name in metadata.get("columns", [])]
        if not available:
            raise ValueError("E_REFERENCE_EMPTY:结果资产没有可引用的列")

        requested = list(columns) if columns else available
        missing = [name for name in requested if name not in available]
        if missing:
            # Do not silently drop: the model would otherwise assume they exist.
            raise ValueError(f"E_REFERENCE_COLUMN:结果资产不存在列 {missing}")

        selected = requested[: self.max_columns]
        positions = [available.index(name) for name in selected]

        source_rows = metadata.get("rows", []) or []
        row_budget = max_rows if max_rows is not None else self.max_rows
        row_budget = max(0, min(int(row_budget), self.max_rows))

        sliced: list[tuple[Any, ...]] = []
        used_bytes = 0
        truncated = False

        for row in source_rows:
            if len(sliced) >= row_budget:
                truncated = True
                break
            values = tuple(row[position] if position < len(row) else None for position in positions)
            size = sum(len(str(value).encode("utf-8")) for value in values)
            if used_bytes + size > self.max_context_bytes:
                truncated = True
                break
            sliced.append(values)
            used_bytes += size

        if len(source_rows) > len(sliced):
            truncated = True

        return AssetReference(
            asset_id=asset.id,
            task_id=asset.task_id,
            kind=TABLE,
            columns=tuple(selected),
            rows=tuple(sliced),
            total_rows=len(source_rows),
            total_columns=len(available),
            truncated=truncated,
            semantic_version=asset.semantic_version,
            data_version=asset.data_version,
            referenced_at=now,
        )

    def to_model_context(self, reference: AssetReference) -> str:
        """Render a compact, bounded text block for a prompt.

        Only values that already passed authorization and redaction are used.
        """
        header = " | ".join(reference.columns) if reference.columns else ""
        lines = [header] if header else []
        for row in reference.rows:
            lines.append(" | ".join("" if value is None else str(value) for value in row))
        body = "\n".join(lines)
        if len(body.encode("utf-8")) > self.max_context_bytes:
            body = body.encode("utf-8")[: self.max_context_bytes].decode("utf-8", "ignore")
        return body
