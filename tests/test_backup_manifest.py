import json

import pytest

from scripts.operations.backup_manifest import create_manifest, verify_manifest


def test_backup_manifest_round_trip(tmp_path):
    backup = tmp_path / "query_agent.sql"
    backup.write_bytes(b"-- MySQL dump\nCREATE TABLE users (id INT);\n")

    manifest = create_manifest(backup)

    assert manifest["format"] == "query-pilot-mysql-backup"
    assert manifest["size_bytes"] == backup.stat().st_size
    assert verify_manifest(backup)["sha256"] == manifest["sha256"]


def test_backup_manifest_rejects_tampering(tmp_path):
    backup = tmp_path / "query_agent.sql"
    backup.write_bytes(b"original")
    create_manifest(backup)
    backup.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_manifest(backup)


def test_backup_manifest_rejects_invalid_payload(tmp_path):
    backup = tmp_path / "query_agent.sql"
    backup.write_bytes(b"dump")
    manifest = tmp_path / "query_agent.sql.manifest.json"
    manifest.write_text(json.dumps({"format": "wrong"}), encoding="utf-8")

    with pytest.raises(ValueError, match="format or version"):
        verify_manifest(backup, manifest)


def test_backup_manifest_requires_nonempty_backup(tmp_path):
    (tmp_path / "empty.sql").touch()

    with pytest.raises(ValueError, match="empty"):
        create_manifest(tmp_path / "empty.sql")
