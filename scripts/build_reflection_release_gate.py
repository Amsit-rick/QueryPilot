"""Combine reflection action, result and security reports into one release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "query-pilot-reflection-release-gate"
VERSION = 1
MIN_FINAL_RESULT_ACCURACY = 0.95


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid reflection report: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"reflection report must be an object: {path}")
    return payload


def _input(path: Path, payload: dict) -> dict:
    return {
        "file": path.name,
        "sha256": _sha256(path),
        "dataset_version": str(payload.get("dataset_version") or ""),
        "passed": payload.get("gate", {}).get("passed") is True,
    }


def build_release_gate(*, action_path: Path, result_path: Path, security_path: Path) -> dict:
    action = _load(action_path)
    result = _load(result_path)
    security = _load(security_path)
    action_metrics = action.get("modes", {}).get("rules_plus_critic", {})
    result_metrics = result.get("metrics", {})
    reason_codes: list[str] = []

    inputs = {
        "action": _input(action_path, action),
        "result": _input(result_path, result),
        "security": _input(security_path, security),
    }
    expected_prefixes = {
        "action": "reflection-eval-",
        "result": "reflection-result-eval-",
        "security": "reflection-security-eval-",
    }
    for name, item in inputs.items():
        if not item["dataset_version"].startswith(expected_prefixes[name]):
            reason_codes.append(f"{name.upper()}_DATASET_VERSION_INVALID")
        if not item["passed"]:
            reason_codes.append(f"{name.upper()}_GATE_FAILED")

    action_bypasses = int(action_metrics.get("security_permission_bypasses", 0) or 0)
    result_bypasses = int(result_metrics.get("security_permission_bypasses", 0) or 0)
    security_bypasses = int(security.get("security_permission_bypasses", 0) or 0)
    sensitive_value_leaks = int(security.get("sensitive_value_leaks", 0) or 0)
    dangerous_actions = int(security.get("dangerous_actions", 0) or 0)
    final_result_accuracy = float(result_metrics.get("final_result_accuracy", 0) or 0)

    if final_result_accuracy < MIN_FINAL_RESULT_ACCURACY:
        reason_codes.append("RESULT_ACCURACY_BELOW_THRESHOLD")
    if action_bypasses + result_bypasses + security_bypasses > 0:
        reason_codes.append("SECURITY_PERMISSION_BYPASS")
    if sensitive_value_leaks > 0:
        reason_codes.append("SENSITIVE_VALUE_LEAK")
    if dangerous_actions > 0:
        reason_codes.append("DANGEROUS_ACTION")

    return {
        "format": FORMAT,
        "version": VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": not reason_codes,
        "reason_codes": reason_codes,
        "inputs": inputs,
        "metrics": {
            "classification_accuracy": float(action_metrics.get("classification_accuracy", 0) or 0),
            "reflection_action_accuracy": float(result_metrics.get("reflection_action_accuracy", 0) or 0),
            "final_result_accuracy": final_result_accuracy,
            "security_permission_bypasses": action_bypasses + result_bypasses + security_bypasses,
            "sensitive_value_leaks": sensitive_value_leaks,
            "dangerous_actions": dangerous_actions,
        },
        "thresholds": {"final_result_accuracy_min": MIN_FINAL_RESULT_ACCURACY},
        "limitations": [
            "This gate binds local evaluation reports; it is not enterprise production accuracy evidence.",
            "Input hashes detect report replacement inside the release evidence chain.",
        ],
    }


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the composite reflection release gate")
    parser.add_argument("--action", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--security", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/reflection-release-gate.json"))
    args = parser.parse_args(argv)
    try:
        payload = build_release_gate(
            action_path=args.action,
            result_path=args.result,
            security_path=args.security,
        )
        _write_atomic(args.output, payload)
    except (OSError, ValueError) as exc:
        print(f"Reflection release gate failed: {exc}")
        return 2
    print(f"Reflection release gate written: {args.output}")
    if not payload["passed"]:
        for reason in payload["reason_codes"]:
            print(f"- {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
