"""Run one explicitly authorized proactive analysis scan."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.proactive_analysis_runtime import get_proactive_analysis_runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one bounded proactive analysis pass.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Required acknowledgement because this command can create query tasks and approvals.",
    )
    parser.add_argument("--limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print("E_PROACTIVE_EXECUTE_REQUIRED", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(get_proactive_analysis_runner().run_once(limit=args.limit))
    except Exception as exc:
        candidate = str(exc).split(":", 1)[0]
        error_code = candidate if candidate.startswith("E_") else "E_PROACTIVE_RUNNER"
        print(json.dumps({"ok": False, "error_code": error_code}, ensure_ascii=True))
        return 1
    print(json.dumps({"ok": True, **asdict(report)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
