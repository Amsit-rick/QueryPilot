"""Best-effort Redis checkpoint storage for agent execution state."""
import json
from typing import Any

from app.domain.agent_recovery import CHECKPOINT_SCHEMA_VERSION


def save_checkpoint(redis_url: str, thread_id: str, snapshot: dict[str, Any], ttl: int = 3600) -> bool:
    if not thread_id or not redis_url:
        return False
    try:
        import redis
        client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=0.25, socket_timeout=0.25)
        envelope = {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "snapshot": snapshot,
        }
        client.setex(
            f"query-agent:checkpoint:{thread_id}",
            ttl,
            json.dumps(envelope, ensure_ascii=False),
        )
        return True
    except Exception:
        return False


def load_checkpoint(redis_url: str, thread_id: str) -> dict[str, Any] | None:
    if not thread_id or not redis_url:
        return None
    try:
        import redis
        raw = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=0.25, socket_timeout=0.25).get(f"query-agent:checkpoint:{thread_id}")
        if not raw:
            return None
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            return None
        if int(envelope.get("schema_version", 0) or 0) != CHECKPOINT_SCHEMA_VERSION:
            return None
        snapshot = envelope.get("snapshot")
        return snapshot if isinstance(snapshot, dict) else None
    except Exception:
        return None
