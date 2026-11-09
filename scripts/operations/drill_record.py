"""Create and verify evidence-backed operational drill records."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

FORMAT = "query-pilot-operations-drill"
VERSION = 1
DRILL_TYPES = {
    "mysql_restore",
    "database_failover",
    "redis_failover",
    "migration_rollback",
    "service_recovery",
}
OUTCOMES = {"passed", "failed", "blocked"}
CHECK_STATUSES = {"passed", "failed", "blocked", "not_run"}


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_facts(facts: object) -> dict:
    if not isinstance(facts, dict):
        raise ValueError("drill input must be a JSON object")
    required_text = ("drill_id", "environment", "started_at", "ended_at")
    for field in required_text:
        if not isinstance(facts.get(field), str) or not facts[field].strip():
            raise ValueError(f"{field} is required")
    drill_type = facts.get("drill_type")
    outcome = facts.get("outcome")
    if drill_type not in DRILL_TYPES:
        raise ValueError(f"drill_type must be one of: {', '.join(sorted(DRILL_TYPES))}")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(sorted(OUTCOMES))}")
    started = _timestamp(facts["started_at"], "started_at")
    ended = _timestamp(facts["ended_at"], "ended_at")
    if ended < started:
        raise ValueError("ended_at must not be earlier than started_at")
    checks = facts.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must contain at least one result")
    normalized_checks = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"checks[{index}] must be an object")
        name = check.get("name")
        status = check.get("status")
        detail = check.get("detail", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"checks[{index}].name is required")
        if status not in CHECK_STATUSES:
            raise ValueError(f"checks[{index}].status is invalid")
        if not isinstance(detail, str):
            raise ValueError(f"checks[{index}].detail must be text")
        normalized_checks.append({"name": name.strip(), "status": status, "detail": detail.strip()})
    if outcome == "passed" and any(item["status"] != "passed" for item in normalized_checks):
        raise ValueError("a passed drill cannot contain non-passed checks")
    for field in ("rpo_seconds", "rto_seconds"):
        value = facts.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise ValueError(f"{field} must be a non-negative integer")
    evidence_files = facts.get("evidence_files", [])
    if not isinstance(evidence_files, list) or any(not isinstance(item, str) for item in evidence_files):
        raise ValueError("evidence_files must be a list of paths")
    if outcome == "passed" and not evidence_files:
        raise ValueError("a passed drill requires at least one evidence file")
    return {**facts, "checks": normalized_checks, "evidence_files": evidence_files}


def create_record(facts: object, *, base_dir: Path) -> dict:
    normalized = _validate_facts(facts)
    base_dir = base_dir.resolve()
    evidence = []
    evidence_names = set()
    for raw_path in normalized.pop("evidence_files"):
        path = Path(raw_path)
        if not path.is_absolute():
            path = base_dir / path
        path = path.resolve()
        if not path.is_relative_to(base_dir):
            raise ValueError(f"evidence file must stay within base_dir: {raw_path}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"evidence file is missing or empty: {raw_path}")
        if path.name in evidence_names:
            raise ValueError(f"evidence basenames must be unique: {path.name}")
        evidence_names.add(path.name)
        evidence.append(
            {
                "file": Path(raw_path).name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "format": FORMAT,
        "version": VERSION,
        **normalized,
        "evidence": evidence,
        "limitations": "This record reflects supplied drill facts and local evidence; approval remains external.",
    }


def verify_record(record: object, *, evidence_dir: Path | None = None) -> list[str]:
    problems = []
    evidence_root = evidence_dir.resolve() if evidence_dir is not None else None
    if not isinstance(record, dict):
        return ["drill record must be a JSON object"]
    if record.get("format") != FORMAT or record.get("version") != VERSION:
        problems.append("drill record format or version is unsupported")
    try:
        facts = {key: value for key, value in record.items() if key not in {"format", "version", "evidence", "limitations"}}
        facts["evidence_files"] = [item.get("file", "") for item in record.get("evidence", []) if isinstance(item, dict)]
        _validate_facts(facts)
    except ValueError as exc:
        problems.append(str(exc))
    evidence = record.get("evidence")
    if not isinstance(evidence, list):
        problems.append("evidence must be a list")
        return problems
    if record.get("outcome") == "passed" and evidence_dir is None:
        problems.append("evidence_dir is required to verify a passed drill")
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            problems.append(f"evidence[{index}] must be an object")
            continue
        name, size, expected_hash = item.get("file"), item.get("size_bytes"), item.get("sha256")
        if not isinstance(name, str) or not name or Path(name).name != name:
            problems.append(f"evidence[{index}].file must be a basename")
            continue
        if not isinstance(size, int) or size < 1 or not isinstance(expected_hash, str) or len(expected_hash) != 64:
            problems.append(f"evidence[{index}] integrity metadata is invalid")
            continue
        if evidence_dir is not None:
            path = (evidence_root / name).resolve()
            if not path.is_relative_to(evidence_root):
                problems.append(f"evidence file escapes evidence_dir: {name}")
            elif not path.is_file():
                problems.append(f"evidence file is missing: {name}")
            elif path.stat().st_size != size or _sha256_file(path).lower() != expected_hash.lower():
                problems.append(f"evidence file integrity mismatch: {name}")
    return problems


def _write_json(payload: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
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
    parser = argparse.ArgumentParser(description="Create or verify an operations drill record")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("input", type=Path, help="JSON facts captured during the drill")
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("record", type=Path)
    verify.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            facts = json.loads(args.input.read_text(encoding="utf-8"))
            protected = {args.input.resolve()}
            if isinstance(facts, dict) and isinstance(facts.get("evidence_files"), list):
                for raw_path in facts["evidence_files"]:
                    if isinstance(raw_path, str):
                        path = Path(raw_path)
                        protected.add((path if path.is_absolute() else args.input.parent / path).resolve())
            if args.output.resolve() in protected:
                raise ValueError("output path must not overwrite drill input or evidence")
            record = create_record(facts, base_dir=args.input.parent)
            _write_json(record, args.output)
            print(f"Drill record written: {args.output}")
            return 0
        record = json.loads(args.record.read_text(encoding="utf-8"))
        problems = verify_record(record, evidence_dir=args.evidence_dir)
        if problems:
            print("Drill record verification failed:")
            for problem in problems:
                print(f"- {problem}")
            return 1
        print(f"Drill record verified: {args.record}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Drill record failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
