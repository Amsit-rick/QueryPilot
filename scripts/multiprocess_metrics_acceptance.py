"""Start isolated Uvicorn workers and verify Prometheus aggregation over HTTP."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

import httpx


FORMAT = "query-pilot-multiprocess-metrics-acceptance"
VERSION = 1
REQUIRED_CHECKS = (
    "server_started",
    "health_reports_multiprocess",
    "queries_succeeded",
    "multiple_worker_metric_files",
    "metrics_aggregated",
    "cleanup_completed",
)


def evaluate_checks(checks: dict[str, bool]) -> dict:
    failed = [name for name in REQUIRED_CHECKS if checks.get(name) is not True]
    return {"passed": not failed, "failed_checks": failed}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _cleanup_tree(path: Path, *, attempts: int = 10) -> bool:
    """Remove a worker metrics directory after Windows releases DB handles."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt == attempts - 1:
                return False
            time.sleep(0.1 * (attempt + 1))
    return not path.exists()


def _wait_health(base_url: str, timeout_seconds: float = 30) -> dict | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=1)
            if response.status_code == 200:
                return response.json()
        except Exception:
            time.sleep(0.2)
    return None


def run_acceptance(*, workers: int = 2, requests: int = 24, port: int | None = None) -> dict:
    selected_port = port or _free_port()
    base_url = f"http://127.0.0.1:{selected_port}"
    checks = {name: False for name in REQUIRED_CHECKS}
    process: subprocess.Popen | None = None
    started = time.perf_counter()
    root = Path(tempfile.mkdtemp(prefix="query-pilot-multiproc-"))
    try:
        metrics_dir = root / "metrics"
        metrics_dir.mkdir()
        environment = os.environ.copy()
        environment.update(
            DATABASE_BACKEND="sqlite",
            DATABASE_PATH=str(Path.cwd() / "data" / "demo.db"),
            DEFAULT_DATASOURCE="sqlite-demo",
            AUTH_ENABLED="false",
            METRICS_TOKEN="",
            PROMETHEUS_MULTIPROC_DIR=str(metrics_dir),
            AUDIT_LOG_PATH=str(root / "audit.jsonl"),
            AGENT_TRACE_PATH=str(root / "agent-trace.jsonl"),
        )
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(selected_port),
            "--workers",
            str(workers),
            "--log-level",
            "warning",
        ]
        popen_kwargs = {
            "cwd": Path.cwd(),
            "env": environment,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
        health = _wait_health(base_url)
        checks["server_started"] = health is not None
        checks["health_reports_multiprocess"] = bool(
            health and health.get("metrics", {}).get("mode") == "multiprocess"
        )
        if not checks["server_started"]:
            raise RuntimeError("isolated multi-worker server did not start")

        def query(_index: int) -> bool:
            try:
                response = httpx.post(
                    f"{base_url}/api/v1/query",
                    json={
                        "question": "multiprocess metrics acceptance",
                        "sql": "SELECT region, amount FROM sales LIMIT 2",
                    },
                    headers={"Connection": "close"},
                    timeout=15,
                )
                return response.status_code == 200
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=min(requests, 12)) as executor:
            outcomes = list(executor.map(query, range(requests)))
        success_count = sum(outcomes)
        checks["queries_succeeded"] = success_count == requests
        counter_files = list(metrics_dir.glob("counter_*.db"))
        checks["multiple_worker_metric_files"] = len(counter_files) >= 2

        metrics = httpx.get(f"{base_url}/metrics", timeout=10)
        match = re.search(r"^query_pilot_requests_total\s+([0-9.]+)$", metrics.text, re.MULTILINE)
        aggregated = float(match.group(1)) if match else 0.0
        checks["metrics_aggregated"] = metrics.status_code == 200 and aggregated >= success_count
    finally:
        _stop_tree(process)
        checks["cleanup_completed"] = _cleanup_tree(root)

    assessment = evaluate_checks(checks)
    return {
        "format": FORMAT,
        "version": VERSION,
        "external_validation": "executed",
        "passed": assessment["passed"],
        "failed_checks": assessment["failed_checks"],
        "checks": checks,
        "workers": workers,
        "requests": requests,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "limitations": [
            "This is an isolated local Uvicorn acceptance run, not a production orchestrator or load balancer test.",
            "Prometheus scraping, remote storage and Alertmanager delivery are outside this check.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--requests", type=int, default=24)
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, default=Path("artifacts/multiprocess-metrics-acceptance.json"))
    args = parser.parse_args(argv)
    try:
        report = run_acceptance(workers=max(2, args.workers), requests=max(2, args.requests), port=args.port)
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
