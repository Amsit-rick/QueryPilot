import json
import sys
import types

from app.export_queue import enqueue_export, dequeue_export, QUEUE_KEY

class FakeRedis:
    items = []
    def rpush(self, key, value):
        assert key == QUEUE_KEY
        self.items.append(value)
    def blpop(self, key, timeout=0):
        assert key == QUEUE_KEY
        return (key, self.items.pop(0)) if self.items else None

def test_enqueue_and_dequeue(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda url, decode_responses=True: fake)))
    enqueue_export("redis://localhost", "job-1")
    assert json.loads(fake.items[0]) == {"job_id": "job-1"}
    assert dequeue_export("redis://localhost") == "job-1"
