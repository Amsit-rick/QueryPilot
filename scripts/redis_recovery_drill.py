"""Run an isolated Redis restart drill for recovery lease and checkpoint safety."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.domain.agent_recovery import AgentCheckpoint, RecoveryLeaseError
from app.services.agent_recovery_lease_service import AgentRecoveryLeaseService, RedisAgentRecoveryBackend


FORMAT = "query-pilot-redis-recovery-drill"
VERSION = 1
REQUIRED_CHECKS = (
    "redis_started",
    "live_lease_exclusion",
    "outage_observed",
    "redis_restarted",
    "checkpoint_restored",
    "fencing_token_monotonic",
    "stale_holder_rejected",
    "new_holder_committed",
)


def evaluate_drill(checks: dict[str, bool]) -> dict:
    failed = [name for name in REQUIRED_CHECKS if checks.get(name) is not True]
    return {"passed": not failed, "failed_checks": failed}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_redis(executable: Path, workdir: Path, port: int) -> subprocess.Popen:
    command = [
        str(executable),
        "--bind", "127.0.0.1",
        "--port", str(port),
        "--protected-mode", "no",
        "--appendonly", "yes",
        "--appendfsync", "always",
        "--save", "",
        "--dir", str(workdir),
        "--logfile", str(workdir / "redis.log"),
    ]
    return subprocess.Popen(command, cwd=workdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def run_drill(*, redis_server: Path, port: int | None = None) -> dict:
    if not redis_server.is_file():
        raise FileNotFoundError(f"redis-server not found: {redis_server}")
    selected_port = port or _free_port()
    redis_url = f"redis://127.0.0.1:{selected_port}/0"
    checks = {name: False for name in REQUIRED_CHECKS}
    details: dict[str, object] = {"port": selected_port}
    process: subprocess.Popen | None = None
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="query-pilot-redis-drill-") as temporary:
        workdir = Path(temporary)
        try:
            process = _start_redis(redis_server, workdir, selected_port)
            checks["redis_started"] = _wait_ready(redis_url)
            if not checks["redis_started"]:
                raise RuntimeError("isolated Redis did not become ready")

            service = AgentRecoveryLeaseService(
                backend=RedisAgentRecoveryBackend(redis_url=redis_url, checkpoint_ttl_seconds=120),
                lease_seconds=2,
            )
            first = service.acquire(task_id="drill-task", tenant_id="drill-tenant", holder_id="worker-a")
            try:
                service.acquire(task_id="drill-task", tenant_id="drill-tenant", holder_id="worker-b")
            except RecoveryLeaseError as exc:
                checks["live_lease_exclusion"] = exc.code == "E_RECOVERY_LEASE_HELD"
            first_checkpoint = AgentCheckpoint.create(
                task_id="drill-task",
                tenant_id="drill-tenant",
                execution_id="drill-exec-1",
                node_cursor="verify",
                fencing_token=first.fencing_token,
                snapshot={"status": "verified", "params": {"secret": "must-not-persist"}},
            )
            service.commit(first, first_checkpoint)

            _stop_process(process)
            process = None
            checks["outage_observed"] = not _wait_ready(redis_url, timeout_seconds=0.5)

            process = _start_redis(redis_server, workdir, selected_port)
            checks["redis_restarted"] = _wait_ready(redis_url)
            restored = service.load(task_id="drill-task", tenant_id="drill-tenant")
            checks["checkpoint_restored"] = restored == first_checkpoint and "secret" not in str(restored)

            time.sleep(3.1)
            second = service.acquire(task_id="drill-task", tenant_id="drill-tenant", holder_id="worker-b")
            checks["fencing_token_monotonic"] = second.fencing_token > first.fencing_token
            try:
                service.commit(first, first_checkpoint)
            except RecoveryLeaseError as exc:
                checks["stale_holder_rejected"] = exc.code == "E_RECOVERY_FENCING_STALE"

            second_checkpoint = AgentCheckpoint.create(
                task_id="drill-task",
                tenant_id="drill-tenant",
                execution_id="drill-exec-2",
                node_cursor="materialize",
                fencing_token=second.fencing_token,
                snapshot={"status": "completed"},
            )
            service.commit(second, second_checkpoint)
            checks["new_holder_committed"] = service.load(
                task_id="drill-task", tenant_id="drill-tenant"
            ) == second_checkpoint
            details["first_fencing_token"] = first.fencing_token
            details["second_fencing_token"] = second.fencing_token
        finally:
            _stop_process(process)

    assessment = evaluate_drill(checks)
    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "external_validation": "executed",
        "passed": assessment["passed"],
        "failed_checks": assessment["failed_checks"],
        "checks": checks,
        "details": details,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "limitations": [
            "This is an isolated single-process restart drill, not Redis Sentinel or Cluster failover evidence.",
            "The drill uses a temporary port and directory and never stops an existing Redis process.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-server", type=Path, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, default=Path("artifacts/redis-recovery-drill.json"))
    args = parser.parse_args(argv)
    try:
        report = run_drill(redis_server=args.redis_server, port=args.port)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        print(f"Redis recovery drill failed: {type(exc).__name__}: {exc}")
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
