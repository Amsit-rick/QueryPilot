from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.rotate_local_mysql_credentials import (
    AccountCredential,
    CredentialRotationError,
    RotationSettings,
    generate_credentials,
    load_rotation_settings,
    rotate_local_mysql_credentials,
    verify_backup_evidence,
)
from scripts.operations.backup_manifest import create_manifest


@dataclass
class FakeBackend:
    passwords: dict[str, str]
    fail_on_change: str | None = None

    def __post_init__(self) -> None:
        self.changes: list[tuple[str, str, str]] = []
        self.verifications: list[tuple[str, str]] = []

    def change_password(
        self,
        account: AccountCredential,
        current_password: str,
        new_password: str,
    ) -> None:
        if self.fail_on_change == account.category:
            self.fail_on_change = None
            raise RuntimeError("simulated database failure")
        assert self.passwords[account.category] == current_password
        self.passwords[account.category] = new_password
        self.changes.append((account.category, current_password, new_password))

    def verify(self, account: AccountCredential, password: str) -> bool:
        self.verifications.append((account.category, password))
        return self.passwords[account.category] == password


def _env_text() -> str:
    return (
        "MODEL_API_KEY=keep-model-key\n"
        "MYSQL_HOST=127.0.0.1\n"
        "MYSQL_PORT=3307\n"
        "MYSQL_DATABASE=query_agent\n"
        "MYSQL_USER=query_app\n"
        "MYSQL_PASSWORD=old-control-secret\n"
        "MYSQL_ROOT_PASSWORD=old-root-secret\n"
        "MYSQL_QUERY_USER=query_readonly\n"
        "MYSQL_QUERY_PASSWORD=old-query-secret\n"
    )


def _settings() -> RotationSettings:
    return RotationSettings(
        host="127.0.0.1",
        port=3307,
        database="query_agent",
        accounts=(
            AccountCredential("query", "query_readonly", "MYSQL_QUERY_PASSWORD", "old-query-secret"),
            AccountCredential("control", "query_app", "MYSQL_PASSWORD", "old-control-secret"),
            AccountCredential("root", "root", "MYSQL_ROOT_PASSWORD", "old-root-secret"),
        ),
    )


def _backend() -> FakeBackend:
    return FakeBackend(
        {
            "query": "old-query-secret",
            "control": "old-control-secret",
            "root": "old-root-secret",
        }
    )


def test_generate_credentials_returns_three_distinct_high_entropy_values():
    generated = generate_credentials()

    assert set(generated) == {"query", "control", "root"}
    assert len(set(generated.values())) == 3
    assert all(len(value) >= 32 for value in generated.values())


def test_load_settings_uses_legacy_control_password_only_when_root_is_missing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(_env_text().replace("MYSQL_ROOT_PASSWORD=old-root-secret\n", ""), encoding="utf-8")

    settings = load_rotation_settings(env_path)

    by_category = {account.category: account for account in settings.accounts}
    assert by_category["root"].password == "old-control-secret"
    assert by_category["control"].password == "old-control-secret"


def test_success_rotates_query_control_root_then_atomically_updates_env_and_redacted_report(tmp_path):
    env_path = tmp_path / ".env"
    report_path = tmp_path / "rotation.json"
    env_path.write_text(_env_text(), encoding="utf-8")
    backend = _backend()
    generated = {
        "query": "new-query-secret-abcdefghijklmnopqrstuvwxyz",
        "control": "new-control-secret-abcdefghijklmnopqrstuvwxyz",
        "root": "new-root-secret-abcdefghijklmnopqrstuvwxyz",
    }

    report = rotate_local_mysql_credentials(
        settings=_settings(),
        backend=backend,
        env_path=env_path,
        report_path=report_path,
        generated=generated,
    )

    assert [change[0] for change in backend.changes] == ["query", "control", "root"]
    assert [item[0] for item in backend.verifications] == ["query", "control", "root"]
    rendered_env = env_path.read_text(encoding="utf-8")
    assert "MODEL_API_KEY=keep-model-key" in rendered_env
    assert "MYSQL_QUERY_PASSWORD=" + generated["query"] in rendered_env
    assert "MYSQL_PASSWORD=" + generated["control"] in rendered_env
    assert "MYSQL_ROOT_PASSWORD=" + generated["root"] in rendered_env
    assert report["status"] == "succeeded"
    report_text = report_path.read_text(encoding="utf-8")
    assert json.loads(report_text)["status"] == "succeeded"
    assert not any(secret in report_text for secret in generated.values())
    assert not any(secret in report_text for secret in backend.passwords.values())


def test_database_failure_rolls_back_completed_accounts_and_does_not_modify_env(tmp_path):
    env_path = tmp_path / ".env"
    report_path = tmp_path / "rotation.json"
    original = _env_text()
    env_path.write_text(original, encoding="utf-8")
    backend = _backend()
    backend.fail_on_change = "control"

    with pytest.raises(CredentialRotationError, match="database rotation failed"):
        rotate_local_mysql_credentials(
            settings=_settings(),
            backend=backend,
            env_path=env_path,
            report_path=report_path,
        )

    assert env_path.read_text(encoding="utf-8") == original
    assert backend.passwords == {
        "query": "old-query-secret",
        "control": "old-control-secret",
        "root": "old-root-secret",
    }
    assert [change[0] for change in backend.changes] == ["query", "query"]
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_env_write_failure_uses_new_credentials_to_roll_back_database(tmp_path):
    env_path = tmp_path / ".env"
    report_path = tmp_path / "rotation.json"
    original = _env_text()
    env_path.write_text(original, encoding="utf-8")
    backend = _backend()

    def fail_env_write(_path: Path, _updates: dict[str, str]) -> None:
        raise OSError("simulated env write failure")

    with pytest.raises(CredentialRotationError, match="environment update failed"):
        rotate_local_mysql_credentials(
            settings=_settings(),
            backend=backend,
            env_path=env_path,
            report_path=report_path,
            env_writer=fail_env_write,
        )

    assert env_path.read_text(encoding="utf-8") == original
    assert backend.passwords == {
        "query": "old-query-secret",
        "control": "old-control-secret",
        "root": "old-root-secret",
    }
    assert [change[0] for change in backend.changes] == [
        "query",
        "control",
        "root",
        "root",
        "control",
        "query",
    ]
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_dry_run_never_changes_database_or_environment(tmp_path):
    env_path = tmp_path / ".env"
    report_path = tmp_path / "rotation.json"
    original = _env_text()
    env_path.write_text(original, encoding="utf-8")
    backend = _backend()

    report = rotate_local_mysql_credentials(
        settings=_settings(),
        backend=backend,
        env_path=env_path,
        report_path=report_path,
        dry_run=True,
    )

    assert report["status"] == "dry_run"
    assert backend.changes == []
    assert env_path.read_text(encoding="utf-8") == original
    assert "secret" not in report_path.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("username", ["query_app; DROP USER root", "bad user", "root@localhost", ""])
def test_account_identifiers_reject_sql_injection(username):
    settings = _settings()
    accounts = list(settings.accounts)
    accounts[1] = AccountCredential("control", username, "MYSQL_PASSWORD", "old-control-secret")

    with pytest.raises(ValueError, match="database username"):
        RotationSettings(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            accounts=tuple(accounts),
        ).validate()


def test_failure_report_and_error_never_contain_database_passwords(tmp_path, capsys):
    env_path = tmp_path / ".env"
    report_path = tmp_path / "rotation.json"
    env_path.write_text(_env_text(), encoding="utf-8")
    backend = _backend()
    backend.fail_on_change = "control"

    with pytest.raises(CredentialRotationError) as raised:
        rotate_local_mysql_credentials(
            settings=_settings(),
            backend=backend,
            env_path=env_path,
            report_path=report_path,
        )

    captured = capsys.readouterr()
    combined = str(raised.value) + captured.out + captured.err + report_path.read_text(encoding="utf-8")
    for secret in ("old-query-secret", "old-control-secret", "old-root-secret"):
        assert secret not in combined


def test_backup_evidence_requires_a_valid_local_manifest(tmp_path):
    backup = tmp_path / "query_agent.sql"
    backup.write_text("-- MySQL dump\nCREATE TABLE schema_migrations (version varchar(50));\n", encoding="utf-8")
    manifest = tmp_path / "query_agent.sql.manifest.json"
    create_manifest(backup, manifest)

    assert verify_backup_evidence(manifest) == backup


def test_backup_evidence_rejects_path_traversal(tmp_path):
    manifest = tmp_path / "query_agent.sql.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "query-pilot-mysql-backup",
                "version": 1,
                "backup_file": "../outside.sql",
                "size_bytes": 1,
                "sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="backup_file"):
        verify_backup_evidence(manifest)
