"""Create and verify tamper-evident metadata for MySQL dump files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "query-pilot-mysql-backup"
VERSION = 1
CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _require_backup(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"backup file not found: {path}")
    if not path.is_file():
        raise ValueError(f"backup path is not a file: {path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"backup file is empty: {path}")
    return path


def default_manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}.manifest.json")


def create_manifest(backup_path: Path, manifest_path: Path | None = None) -> dict:
    backup = _require_backup(backup_path)
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "backup_file": backup.name,
        "size_bytes": backup.stat().st_size,
        "sha256": sha256_file(backup),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    target = manifest_path or default_manifest_path(backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
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
    return manifest


def verify_manifest(backup_path: Path, manifest_path: Path | None = None) -> dict:
    backup = _require_backup(backup_path)
    source = manifest_path or default_manifest_path(backup)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"manifest file not found: {source}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest is not valid JSON: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("manifest format or version is unsupported")
    expected_size = payload.get("size_bytes")
    expected_hash = payload.get("sha256")
    if not isinstance(expected_size, int) or expected_size < 1:
        raise ValueError("manifest size_bytes is invalid")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError("manifest sha256 is invalid")
    actual_size = backup.stat().st_size
    actual_hash = sha256_file(backup)
    if actual_size != expected_size:
        raise ValueError(f"backup size mismatch: expected {expected_size}, got {actual_size}")
    if actual_hash.lower() != expected_hash.lower():
        raise ValueError("backup sha256 mismatch")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify a Query Pilot backup manifest")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="write a SHA-256 manifest")
    create.add_argument("backup", type=Path)
    create.add_argument("--manifest", type=Path)
    verify = subparsers.add_parser("verify", help="verify a backup against its manifest")
    verify.add_argument("backup", type=Path)
    verify.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_manifest(args.backup, args.manifest)
            target = args.manifest or default_manifest_path(args.backup)
            print(f"Backup manifest written: {target}")
            print(f"SHA-256: {manifest['sha256']}")
        else:
            manifest = verify_manifest(args.backup, args.manifest)
            print(f"Backup manifest verified: {args.manifest or default_manifest_path(args.backup)}")
            print(f"SHA-256: {manifest['sha256']}")
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Backup manifest failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
