"""Run one explicitly authorized, bounded recovery supervisor pass."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.recovery_supervisor_runtime import get_recovery_supervisor_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded Query Pilot recovery supervisor pass.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required acknowledgement because this command may converge task state.",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--stale-after-seconds", type=int, default=120)
    parser.add_argument(
        "--holder-id",
        default=f"supervisor:{socket.gethostname()}:{os.getpid()}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print("E_RECOVERY_EXECUTE_REQUIRED", file=sys.stderr)
        return 2
    try:
        report = get_recovery_supervisor_service().run_once(
            limit=args.limit,
            stale_after_seconds=args.stale_after_seconds,
            holder_id=args.holder_id,
        )
    except Exception as exc:
        candidate = str(exc).split(":", 1)[0]
        error_code = candidate if candidate.startswith("E_") else "E_RECOVERY_SUPERVISOR"
        print(json.dumps({"ok": False, "error_code": error_code}, ensure_ascii=True))
        return 1
    print(json.dumps({"ok": True, **asdict(report)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
