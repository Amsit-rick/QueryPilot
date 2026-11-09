"""Export filtered, redacted audit records without mutating the source logs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MAX_RECORDS = 10_000
MANIFEST_FORMAT = "query-pilot-audit-export"
MANIFEST_VERSION = 1


def _log_paths(source: Path, backup_count: int) -> list[Path]:
    paths = [source]
    for index in range(1, max(0, backup_count) + 1):
        rotated = source.with_name(f"{source.name}.{index}")
        if rotated.exists():
            paths.append(rotated)
    return paths


def _valid_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _scope_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def default_manifest_path(export_path: Path) -> Path:
    return export_path.with_name(f"{export_path.name}.manifest.json")


def create_export_manifest(
    export_path: Path,
    *,
    record_count: int,
    tenant_id: str | None,
    task_id: str | None,
    event: str | None,
    since: str | None,
    until: str | None,
    manifest_path: Path | None = None,
) -> dict:
    """Create a sidecar manifest without repeating raw tenant/task identifiers."""
    if not export_path.is_file():
        raise FileNotFoundError(f"audit export not found: {export_path}")
    target = manifest_path or default_manifest_path(export_path)
    if target.resolve() == export_path.resolve():
        raise ValueError("manifest path must not overwrite the audit export")
    payload = {
        "format": MANIFEST_FORMAT,
        "version": MANIFEST_VERSION,
        "export_file": export_path.name,
        "size_bytes": export_path.stat().st_size,
        "sha256": _sha256_file(export_path),
        "record_count": record_count,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "tenant_id_sha256": _scope_digest(tenant_id),
            "task_id_sha256": _scope_digest(task_id),
            "event": event,
            "since": since,
            "until": until,
        },
    }
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
    return payload


def export_audit(
    source: Path,
    target: Path,
    *,
    tenant_id: str | None = None,
    task_id: str | None = None,
    event: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_records: int = DEFAULT_MAX_RECORDS,
    backup_count: int = 5,
) -> int:
    """Write matching JSON objects as JSONL and return the exported count."""
    if max_records < 1 or max_records > 1_000_000:
        raise ValueError("max_records must be between 1 and 1000000")
    if backup_count < 0 or backup_count > 100:
        raise ValueError("backup_count must be between 0 and 100")
    if tenant_id is None and task_id is None:
        raise ValueError("tenant_id or task_id is required for scoped export")
    start = _valid_timestamp(since)
    end = _valid_timestamp(until)
    if (since and start is None) or (until and end is None):
        raise ValueError("since/until must be ISO-8601 timestamps")
    source_paths = _log_paths(source, backup_count)
    if target.resolve() in {path.resolve() for path in source_paths}:
        raise ValueError("output path must not overwrite a source audit log")
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            reached_limit = False
            for path in source_paths:
                if not path.exists() or reached_limit:
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        if count >= max_records:
                            reached_limit = True
                            break
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(record, dict):
                            continue
                        if tenant_id is not None and record.get("tenant_id") != tenant_id:
                            continue
                        if task_id is not None and record.get("task_id") != task_id:
                            continue
                        if event is not None and record.get("event") != event:
                            continue
                        timestamp = _valid_timestamp(record.get("timestamp"))
                        if start and (timestamp is None or timestamp < start):
                            continue
                        if end and (timestamp is None or timestamp > end):
                            continue
                        output.write(json.dumps(record, ensure_ascii=False) + "\n")
                        count += 1
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export scoped Query Pilot audit records")
    parser.add_argument("--source", type=Path, default=Path("data/audit.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tenant-id")
    parser.add_argument("--task-id")
    parser.add_argument("--event")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    parser.add_argument("--backup-count", type=int, default=5)
    manifest_options = parser.add_mutually_exclusive_group()
    manifest_options.add_argument("--manifest", type=Path, help="custom sidecar manifest path")
    manifest_options.add_argument("--no-manifest", action="store_true", help="skip the SHA-256 sidecar")
    args = parser.parse_args(argv)
    try:
        count = export_audit(
            args.source,
            args.output,
            tenant_id=args.tenant_id,
            task_id=args.task_id,
            event=args.event,
            since=args.since,
            until=args.until,
            max_records=args.max_records,
            backup_count=args.backup_count,
        )
        manifest_target = args.manifest or default_manifest_path(args.output)
        if not args.no_manifest:
            protected_logs = {path.resolve() for path in _log_paths(args.source, args.backup_count)}
            if manifest_target.resolve() in protected_logs:
                raise ValueError("manifest path must not overwrite a source audit log")
            create_export_manifest(
                args.output,
                record_count=count,
                tenant_id=args.tenant_id,
                task_id=args.task_id,
                event=args.event,
                since=args.since,
                until=args.until,
                manifest_path=manifest_target,
            )
    except (OSError, ValueError) as exc:
        print(f"Audit export failed: {exc}")
        return 1
    print(f"Audit export written: {args.output} ({count} records)")
    if not args.no_manifest:
        print(f"Audit export manifest written: {manifest_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
