"""Exercise Schema Embedding shared cache and circuit against isolated Redis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import circuit_breaker
from app.services.schema_embedding_service import (
    EmbeddingProviderError,
    OpenAICompatibleSchemaEmbeddingScorer,
    SchemaEmbeddingConfig,
)


FORMAT = "query-pilot-schema-embedding-redis-drill"
VERSION = 1
REQUIRED_CHECKS = (
    "redis_started",
    "shared_cache_reused",
    "raw_schema_absent",
    "cache_ttl_present",
    "shared_circuit_opened",
    "cache_outage_failed_before_provider",
)


def evaluate_checks(checks: dict[str, bool]) -> dict:
    failed = [name for name in REQUIRED_CHECKS if checks.get(name) is not True]
    return {"passed": not failed, "failed_checks": failed}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_redis(executable: Path, workdir: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(executable),
            "--bind", "127.0.0.1",
            "--port", str(port),
            "--protected-mode", "no",
            "--save", "",
            "--appendonly", "no",
            "--dir", str(workdir),
            "--logfile", str(workdir / "redis.log"),
        ],
        cwd=workdir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(redis_url: str, timeout_seconds: float = 10) -> bool:
    import redis

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if redis.Redis.from_url(redis_url, socket_connect_timeout=0.2, socket_timeout=0.2).ping():
                return True
        except Exception:
            time.sleep(0.1)
    return False


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()


def _success_transport(calls: list[list[str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = [str(value) for value in payload.get("input", [])]
        calls.append(inputs)
        return httpx.Response(
            200,
            json={"data": [{"index": index, "embedding": [1.0, 0.0]} for index, _ in enumerate(inputs)]},
        )

    return httpx.MockTransport(handler)


def run_drill(*, redis_server: Path, port: int | None = None) -> dict:
    if not redis_server.is_file():
        raise FileNotFoundError(f"redis-server not found: {redis_server}")
    selected_port = port or _free_port()
    redis_url = f"redis://127.0.0.1:{selected_port}/0"
    checks = {name: False for name in REQUIRED_CHECKS}
    process: subprocess.Popen | None = None
    started = time.perf_counter()
    circuit_breaker.reset_memory()
    with tempfile.TemporaryDirectory(prefix="query-pilot-embedding-redis-") as temporary:
        workdir = Path(temporary)
        try:
            process = _start_redis(redis_server, workdir, selected_port)
            checks["redis_started"] = _wait_ready(redis_url)
            if not checks["redis_started"]:
                raise RuntimeError("isolated Redis did not become ready")

            calls: list[list[str]] = []
            config = SchemaEmbeddingConfig(
                base_url="https://embedding.invalid/v1",
                model="redis-drill-model",
                api_key_env="EMBEDDING_DRILL_KEY",
                cache_backend="redis",
                cache_ttl_seconds=120,
                redis_url=redis_url,
            )
            documents = {"customer_dimension": "private-schema-marker customer region"}
            first = OpenAICompatibleSchemaEmbeddingScorer(
                config,
                environ={"EMBEDDING_DRILL_KEY": "synthetic-secret"},
                transport=_success_transport(calls),
            )
            second = OpenAICompatibleSchemaEmbeddingScorer(
                config,
                environ={"EMBEDDING_DRILL_KEY": "synthetic-secret"},
                transport=_success_transport(calls),
            )
            first("customer revenue", documents)
            calls_after_first = len(calls)
            second("customer revenue", documents)
            checks["shared_cache_reused"] = calls_after_first == 2 and len(calls) == 3

            import redis

            client = redis.Redis.from_url(redis_url, decode_responses=False)
            keys = list(client.scan_iter(match="query-pilot:schema-embedding:*"))
            values = [client.get(key) or b"" for key in keys]
            marker = b"private-schema-marker"
            checks["raw_schema_absent"] = bool(keys) and all(marker not in key and marker not in value for key, value in zip(keys, values))
            checks["cache_ttl_present"] = bool(keys) and all(client.ttl(key) > 0 for key in keys)

            failing_calls = 0

            def failing_handler(_request: httpx.Request) -> httpx.Response:
                nonlocal failing_calls
                failing_calls += 1
                return httpx.Response(503)

            circuit_config = SchemaEmbeddingConfig(
                base_url="https://embedding.invalid/v1",
                model="redis-circuit-model",
                api_key_env="EMBEDDING_DRILL_KEY",
                cache_backend="memory",
                circuit_backend="redis",
                circuit_failure_threshold=1,
                circuit_reset_seconds=120,
                redis_url=redis_url,
            )
            for _attempt in range(2):
                scorer = OpenAICompatibleSchemaEmbeddingScorer(
                    circuit_config,
                    environ={"EMBEDDING_DRILL_KEY": "synthetic-secret"},
                    transport=httpx.MockTransport(failing_handler),
                )
                try:
                    scorer("customer revenue", documents)
                except EmbeddingProviderError:
                    pass
            checks["shared_circuit_opened"] = failing_calls == 1

            _stop_process(process)
            process = None
            outage_calls = 0

            def outage_handler(_request: httpx.Request) -> httpx.Response:
                nonlocal outage_calls
                outage_calls += 1
                return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

            outage = OpenAICompatibleSchemaEmbeddingScorer(
                SchemaEmbeddingConfig(
                    base_url="https://embedding.invalid/v1",
                    model="redis-outage-model",
                    api_key_env="EMBEDDING_DRILL_KEY",
                    cache_backend="redis",
                    redis_url=redis_url,
                ),
                environ={"EMBEDDING_DRILL_KEY": "synthetic-secret"},
                transport=httpx.MockTransport(outage_handler),
            )
            try:
                outage("customer revenue", documents)
            except EmbeddingProviderError as exc:
                checks["cache_outage_failed_before_provider"] = exc.code == "E_EMBEDDING_CACHE_UNAVAILABLE" and outage_calls == 0
        finally:
            _stop_process(process)

    assessment = evaluate_checks(checks)
    return {
        "format": FORMAT,
        "version": VERSION,
        "external_validation": "executed",
        "passed": assessment["passed"],
        "failed_checks": assessment["failed_checks"],
        "checks": checks,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "limitations": [
            "This uses an isolated single Redis process, not Sentinel or Cluster failover.",
            "The embedding HTTP transport is deterministic synthetic data, not an external provider acceptance.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-server", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, default=Path("artifacts/schema-embedding-redis-drill.json"))
    args = parser.parse_args(argv)
    try:
        report = run_drill(redis_server=args.redis_server, port=args.port)
    except Exception as exc:
        report = {
            "format": FORMAT,
            "version": VERSION,
            "external_validation": "failed",
            "passed": False,
            "error": type(exc).__name__,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
