"""Thread-safe in-memory reflection rollout repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock

from app.domain.reflection_rollout import ReflectionRolloutVersion


class InMemoryReflectionRolloutRepository:
    def __init__(self):
        self._items: dict[str, ReflectionRolloutVersion] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def create(self, version: ReflectionRolloutVersion) -> ReflectionRolloutVersion:
        with self._lock:
            if any(
                item.tenant_id == version.tenant_id
                and item.policy.version == version.policy.version
                for item in self._items.values()
            ):
                raise ValueError("E_REFLECTION_ROLLOUT_VERSION_DUPLICATE:反思灰度版本已存在")
            self._items[version.id] = version
            return version

    def get(self, version_id: str, tenant_id: str) -> ReflectionRolloutVersion | None:
        with self._lock:
            item = self._items.get(version_id)
            return item if item and item.tenant_id == tenant_id else None

    def list(self, tenant_id: str) -> list[ReflectionRolloutVersion]:
        with self._lock:
            values = [item for item in self._items.values() if item.tenant_id == tenant_id]
            return sorted(values, key=lambda item: item.created_at, reverse=True)

    def set_canary(self, version_id: str, tenant_id: str, percentage: int) -> ReflectionRolloutVersion | None:
        with self._lock:
            target = self.get(version_id, tenant_id)
            if target is None:
                return None
            now = datetime.now(timezone.utc)
            for current_id, current in tuple(self._items.items()):
                if current.tenant_id == tenant_id and current.state == "canary" and current_id != version_id:
                    self._items[current_id] = replace(current, state="retired", canary_percent=0, updated_at=now)
            updated = replace(target, state="canary", canary_percent=percentage, updated_at=now)
            self._items[version_id] = updated
            return updated

    def activate(self, version_id: str, tenant_id: str) -> ReflectionRolloutVersion | None:
        with self._lock:
            target = self.get(version_id, tenant_id)
            if target is None:
                return None
            now = datetime.now(timezone.utc)
            for current_id, current in tuple(self._items.items()):
                if current.tenant_id == tenant_id and current_id != version_id and current.state in {"active", "canary"}:
                    self._items[current_id] = replace(current, state="retired", canary_percent=0, updated_at=now)
            updated = replace(target, state="active", canary_percent=0, updated_at=now)
            self._items[version_id] = updated
            return updated

    def retire(self, version_id: str, tenant_id: str) -> ReflectionRolloutVersion | None:
        with self._lock:
            target = self.get(version_id, tenant_id)
            if target is None:
                return None
            updated = replace(target, state="retired", canary_percent=0, updated_at=datetime.now(timezone.utc))
            self._items[version_id] = updated
            return updated


__all__ = ["InMemoryReflectionRolloutRepository"]

