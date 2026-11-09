"""Redis-backed export queue. Import is optional for the default inline mode."""
import json

QUEUE_KEY = "query_pilot:export_jobs"
DEAD_LETTER_KEY = "query_pilot:export_jobs:dead"


def enqueue_export(redis_url: str, job_id: str) -> None:
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError("Redis 模式需要安装 redis 依赖") from exc
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    client.rpush(QUEUE_KEY, json.dumps({"job_id": job_id}))


def dequeue_export(redis_url: str, timeout: int = 5) -> str | None:
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError("Redis 模式需要安装 redis 依赖") from exc
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    item = client.blpop(QUEUE_KEY, timeout=timeout)
    if not item:
        return None
    return json.loads(item[1]).get("job_id")

def enqueue_dead_letter(redis_url: str, job_id: str, error: str) -> None:
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError("Redis 模式需要安装 redis 依赖") from exc
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    client.rpush(DEAD_LETTER_KEY, json.dumps({"job_id": job_id, "error": error}))

def list_dead_letters(redis_url: str, limit: int = 100) -> list[dict]:
    import redis
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    return [json.loads(item) for item in client.lrange(DEAD_LETTER_KEY, 0, max(0, limit - 1))]

def retry_dead_letter(redis_url: str, job_id: str) -> bool:
    import redis
    client = redis.Redis.from_url(redis_url, decode_responses=True)
    for item in client.lrange(DEAD_LETTER_KEY, 0, -1):
        record = json.loads(item)
        if record.get("job_id") == job_id:
            client.lrem(DEAD_LETTER_KEY, 1, item)
            client.rpush(QUEUE_KEY, json.dumps({"job_id": job_id}))
            return True
    return False
