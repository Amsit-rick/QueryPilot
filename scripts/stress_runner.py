"""Bounded HTTP capacity runner with machine-readable percentile gates."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import socket
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RequestResult:
    duration_ms: float
    ok: bool
    status: str
    error_class: str = ""


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 2)


def call(url: str, payload: dict, token: str = "", timeout_seconds: float = 30) -> RequestResult:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response.read()
            status = str(response.status)
            return RequestResult((time.perf_counter() - started) * 1000, 200 <= response.status < 300, status)
    except urllib.error.HTTPError as exc:
        error_class = "http_5xx" if exc.code >= 500 else "http_4xx"
        return RequestResult((time.perf_counter() - started) * 1000, False, str(exc.code), error_class)
    except (TimeoutError, socket.timeout):
        return RequestResult((time.perf_counter() - started) * 1000, False, "timeout", "timeout")
    except urllib.error.URLError:
        return RequestResult((time.perf_counter() - started) * 1000, False, "network_error", "network")
    except Exception:
        return RequestResult((time.perf_counter() - started) * 1000, False, "client_error", "client")


def summarize_results(
    results: list[RequestResult],
    *,
    workers: int,
    elapsed_seconds: float,
    min_success_rate: float,
    p95_max_ms: float,
    p99_max_ms: float,
) -> dict:
    durations = [item.duration_ms for item in results]
    successes = sum(item.ok for item in results)
    total = len(results)
    success_rate = successes / total if total else 0.0
    metrics = {
        "requests": total,
        "workers": workers,
        "successes": successes,
        "failures": total - successes,
        "success_rate": round(success_rate, 6),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "throughput_rps": round(total / elapsed_seconds, 3) if elapsed_seconds > 0 else 0.0,
        "avg_ms": round(sum(durations) / total, 2) if total else 0.0,
        "p50_ms": percentile(durations, 0.50),
        "p95_ms": percentile(durations, 0.95),
        "p99_ms": percentile(durations, 0.99),
        "max_ms": round(max(durations), 2) if durations else 0.0,
    }
    reason_codes = []
    if success_rate < min_success_rate:
        reason_codes.append("SUCCESS_RATE_BELOW_THRESHOLD")
    if metrics["p95_ms"] > p95_max_ms:
        reason_codes.append("P95_LATENCY_EXCEEDED")
    if metrics["p99_ms"] > p99_max_ms:
        reason_codes.append("P99_LATENCY_EXCEEDED")
    return {
        "format": "query-pilot-capacity-report",
        "version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "statuses": dict(sorted(Counter(item.status for item in results).items())),
        "errors": dict(sorted(Counter(item.error_class for item in results if item.error_class).items())),
        "gate": {
            "passed": not reason_codes,
            "reason_codes": reason_codes,
            "thresholds": {
                "success_rate_min": min_success_rate,
                "p95_ms_max": p95_max_ms,
                "p99_ms_max": p99_max_ms,
            },
        },
        "limitations": [
            "This client-side benchmark is a bounded regression test, not a production load certification.",
            "Results depend on the machine, selected database, model mode and network path used for the run.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/query")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--token", default="")
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--p95-max-ms", type=float, default=2000)
    parser.add_argument("--p99-max-ms", type=float, default=5000)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.requests < 1 or args.workers < 1 or args.workers > args.requests:
        parser.error("requests must be positive and workers must be between 1 and requests")
    payload = {
        "question": "容量回归测试",
        "sql": "SELECT region, SUM(amount) AS amount_sum FROM sales GROUP BY region",
    }
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(
            pool.map(
                lambda _: call(args.url, payload, args.token, args.timeout_seconds),
                range(args.requests),
            )
        )
    report = summarize_results(
        results,
        workers=args.workers,
        elapsed_seconds=time.perf_counter() - started,
        min_success_rate=args.min_success_rate,
        p95_max_ms=args.p95_max_ms,
        p99_max_ms=args.p99_max_ms,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
