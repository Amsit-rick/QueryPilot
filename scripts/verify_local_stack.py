"""Read-only smoke checks for a running local Query Pilot stack."""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]


def _request(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET")
    # Local readiness checks must never inherit Windows/OS proxy settings.
    # A proxy can return a valid-looking response even when the loopback
    # service is not listening, producing a dangerous false-positive report.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return int(response.status), response.headers.get_content_type()
    except urllib.error.HTTPError as error:
        # HTTPError is also a response. Preserve its status so /ready=503 is
        # actionable, while still discarding the body.
        content_type = error.headers.get_content_type() if error.headers else ""
        return int(error.code), content_type


def verify_stack(
    *,
    api_url: str = "http://127.0.0.1:8000",
    frontend_url: str = "http://127.0.0.1:5173",
    request: Callable[[str, float], tuple[int, str]] = _request,
    timeout: float = 3.0,
) -> dict:
    """Check API liveness/readiness and the frontend entry page.

    Only status and content type are retained. Response bodies and exception
    messages are intentionally discarded so secrets cannot leak into reports.
    """
    report: dict = {
        "format": "query-pilot-local-stack-smoke",
        "version": 1,
        "passed": [],
        "warnings": [],
        "blocking": [],
        "checks": [],
        "secrets_redacted": True,
    }

    def check(name: str, status: str, detail: str) -> None:
        report["checks"].append({"name": name, "status": status, "detail": detail})
        bucket = "warnings" if status == "warning" else status
        report[bucket].append(detail)

    endpoints = (
        ("api-health", f"{api_url.rstrip('/')}/health", "API health", "application/json"),
        ("api-ready", f"{api_url.rstrip('/')}/ready", "API readiness", "application/json"),
        ("frontend", frontend_url, "前端入口", "text/html"),
    )
    for name, url, label, expected_type in endpoints:
        try:
            status_code, content_type = request(url, timeout)
        except (OSError, urllib.error.URLError, TimeoutError):
            check(name, "blocking", f"{label}不可用")
            continue
        except Exception:
            # A custom transport must not be able to leak its exception text.
            check(name, "blocking", f"{label}检查异常")
            continue
        if status_code != 200:
            check(name, "blocking", f"{label}返回 HTTP {status_code}")
        elif name == "frontend" and content_type != expected_type:
            check(name, "warning", f"{label}已响应，但内容类型不是 {expected_type}")
        else:
            check(name, "passed", f"{label}正常")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local Query Pilot stack smoke check")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5173")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = verify_stack(api_url=args.api_url, frontend_url=args.frontend_url, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
