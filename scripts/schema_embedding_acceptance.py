"""Run a redacted acceptance probe against the configured embedding runtime."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.schema_embedding_runtime import get_schema_embedding_scorer, schema_embedding_runtime_status


REPORT_FORMAT = "query-pilot-schema-embedding-acceptance"
REPORT_VERSION = 1
_SYNTHETIC_DOCUMENTS = {
    "customer_dimension": "customer account region segment",
    "payment_fact": "payment collection amount status",
}


def run_acceptance(scorer=None) -> dict:
    scorer = scorer if scorer is not None else get_schema_embedding_scorer()
    status = schema_embedding_runtime_status() if scorer is None else {"status": "injected"}
    if scorer is None:
        return {
            "format": REPORT_FORMAT,
            "version": REPORT_VERSION,
            "synthetic": True,
            "external_validation": "not_run",
            "gate": {"passed": False, "reason_codes": ["EMBEDDING_NOT_CONFIGURED"]},
            "runtime": status,
        }
    started = perf_counter()
    try:
        first = scorer("customer revenue", dict(_SYNTHETIC_DOCUMENTS))
        second = scorer("customer revenue", dict(_SYNTHETIC_DOCUMENTS))
        valid = (
            set(first) == set(_SYNTHETIC_DOCUMENTS)
            and first == second
            and all(math.isfinite(float(value)) and 0 <= float(value) <= 1 for value in first.values())
        )
        return {
            "format": REPORT_FORMAT,
            "version": REPORT_VERSION,
            "synthetic": True,
            "external_validation": "passed" if valid else "failed",
            "gate": {"passed": valid, "reason_codes": [] if valid else ["EMBEDDING_CONTRACT_INVALID"]},
            "latency_ms": max(0, round((perf_counter() - started) * 1000, 3)),
            "document_count": len(_SYNTHETIC_DOCUMENTS),
            "repeat_consistent": first == second,
            "runtime": schema_embedding_runtime_status(),
        }
    except Exception:
        return {
            "format": REPORT_FORMAT,
            "version": REPORT_VERSION,
            "synthetic": True,
            "external_validation": "failed",
            "gate": {"passed": False, "reason_codes": ["EMBEDDING_PROBE_FAILED"]},
            "latency_ms": max(0, round((perf_counter() - started) * 1000, 3)),
            "document_count": len(_SYNTHETIC_DOCUMENTS),
            "repeat_consistent": False,
            "runtime": schema_embedding_runtime_status(),
        }


def write_report(payload: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the configured Schema Embedding provider")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-external", action="store_true")
    args = parser.parse_args(argv)
    report = run_acceptance()
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["external_validation"] == "failed":
        return 1
    if args.require_external and report["external_validation"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
