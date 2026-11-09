"""Rotate local MySQL credentials with compensating rollback and redacted evidence."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.operations.backup_manifest import verify_manifest


ENV_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
ROTATION_ORDER = ("query", "control", "root")
ENV_KEYS = {
    "query": "MYSQL_QUERY_PASSWORD",
    "control": "MYSQL_PASSWORD",
    "root": "MYSQL_ROOT_PASSWORD",
}


class CredentialRotationError(RuntimeError):
    """Raised with a stable, non-sensitive credential rotation failure."""


@dataclass(frozen=True)
class AccountCredential:
    category: str
    username: str
    env_key: str
    password: str


@dataclass(frozen=True)
class RotationSettings:
    host: str
    port: int
    database: str
    accounts: tuple[AccountCredential, ...]

    def validate(self) -> None:
        if not self.host.strip():
            raise ValueError("database host is required")
        if not 1 <= self.port <= 65535:
            raise ValueError("database port is invalid")
        if not IDENTIFIER_PATTERN.fullmatch(self.database):
            raise ValueError("database name is invalid")
        if tuple(account.category for account in self.accounts) != ROTATION_ORDER:
            raise ValueError("database accounts must use query, control, root order")
        for account in self.accounts:
            if not IDENTIFIER_PATTERN.fullmatch(account.username):
                raise ValueError("database username is invalid")
            if account.env_key != ENV_KEYS[account.category]:
                raise ValueError("database credential mapping is invalid")
            if not account.password:
                raise ValueError(f"current {account.category} database credential is missing")


class CredentialBackend(Protocol):
    def change_password(
        self,
        account: AccountCredential,
        current_password: str,
        new_password: str,
    ) -> None: ...

    def verify(self, account: AccountCredential, password: str) -> bool: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = ENV_PATTERN.match(line)
        if match:
            values[match.group("key")] = match.group("value")
    return values


def _replace_env(text: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    lines: list[str] = []
    for line in text.splitlines():
        match = ENV_PATTERN.match(line)
        key = match.group("key") if match else None
        if key in remaining:
            lines.append(f"{key}={remaining.pop(key)}")
        else:
            lines.append(line)
    if remaining and lines and lines[-1]:
        lines.append("")
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(lines) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = path.stat().st_mode if path.exists() else None
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            os.chmod(temporary_name, previous_mode)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def atomic_update_env(path: Path, updates: dict[str, str]) -> None:
    source = path.read_text(encoding="utf-8")
    _atomic_write_text(path, _replace_env(source, updates))


def _write_report(path: Path, report: dict[str, object]) -> None:
    _atomic_write_text(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def generate_credentials() -> dict[str, str]:
    generated: dict[str, str] = {}
    used: set[str] = set()
    for category in ROTATION_ORDER:
        while True:
            candidate = secrets.token_urlsafe(36)
            if candidate not in used:
                used.add(candidate)
                generated[category] = candidate
                break
    return generated


def verify_backup_evidence(manifest_path: Path) -> Path:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("backup manifest is missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("backup manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("backup manifest must be a JSON object")
    backup_file = payload.get("backup_file")
    if not isinstance(backup_file, str) or not backup_file or Path(backup_file).name != backup_file:
        raise ValueError("backup manifest backup_file is invalid")
    backup_path = manifest_path.parent / backup_file
    verify_manifest(backup_path, manifest_path)
    return backup_path


def load_rotation_settings(env_path: Path) -> RotationSettings:
    if not env_path.is_file():
        raise ValueError(f"local environment is missing: {env_path}")
    values = _env_values(env_path.read_text(encoding="utf-8"))
    control_password = values.get("MYSQL_PASSWORD", "").strip()
    root_password = values.get("MYSQL_ROOT_PASSWORD", "").strip() or control_password
    settings = RotationSettings(
        host=values.get("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=int(values.get("MYSQL_PORT", "3307").strip() or "3307"),
        database=values.get("MYSQL_DATABASE", "query_agent").strip() or "query_agent",
        accounts=(
            AccountCredential(
                "query",
                values.get("MYSQL_QUERY_USER", "query_readonly").strip(),
                ENV_KEYS["query"],
                values.get("MYSQL_QUERY_PASSWORD", "").strip(),
            ),
            AccountCredential(
                "control",
                values.get("MYSQL_USER", "query_app").strip(),
                ENV_KEYS["control"],
                control_password,
            ),
            AccountCredential("root", "root", ENV_KEYS["root"], root_password),
        ),
    )
    settings.validate()
    return settings


class PyMySqlCredentialBackend:
    """Change account passwords through a validated local root connection."""

    def __init__(self, settings: RotationSettings, *, connect_timeout: int = 5) -> None:
        self._settings = settings
        self._connect_timeout = connect_timeout
        self._root_password = next(
            account.password for account in settings.accounts if account.category == "root"
        )

    def _connect(self, username: str, password: str):
        return pymysql.connect(
            host=self._settings.host,
            port=self._settings.port,
            user=username,
            password=password,
            database=self._settings.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.Cursor,
            connect_timeout=self._connect_timeout,
            read_timeout=self._connect_timeout,
            write_timeout=self._connect_timeout,
            autocommit=True,
        )

    def verify(self, account: AccountCredential, password: str) -> bool:
        connection = None
        try:
            connection = self._connect(account.username, password)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        except pymysql.MySQLError:
            return False
        finally:
            if connection is not None:
                connection.close()

    def change_password(
        self,
        account: AccountCredential,
        current_password: str,
        new_password: str,
    ) -> None:
        if not self.verify(account, current_password):
            raise RuntimeError("current database credential verification failed")
        connection = None
        try:
            connection = self._connect("root", self._root_password)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT Host FROM mysql.user WHERE User=%s ORDER BY Host",
                    (account.username,),
                )
                hosts = [str(row[0]) for row in cursor.fetchall()]
                if not hosts:
                    raise RuntimeError("database account was not found")
                clauses = ", ".join(["%s@%s IDENTIFIED BY %s"] * len(hosts))
                parameters: list[str] = []
                for account_host in hosts:
                    parameters.extend((account.username, account_host, new_password))
                cursor.execute(f"ALTER USER {clauses}", tuple(parameters))
            if account.category == "root":
                self._root_password = new_password
        except pymysql.MySQLError as exc:
            raise RuntimeError("database credential change failed") from exc
        finally:
            if connection is not None:
                connection.close()


def _rollback(
    *,
    backend: CredentialBackend,
    completed: list[AccountCredential],
    generated: dict[str, str],
    steps: list[dict[str, str]],
) -> bool:
    rollback_succeeded = True
    for account in reversed(completed):
        try:
            backend.change_password(account, generated[account.category], account.password)
            if not backend.verify(account, account.password):
                raise RuntimeError("rollback verification failed")
            steps.append({"category": account.category, "action": "rollback", "status": "passed"})
        except Exception:
            rollback_succeeded = False
            steps.append({"category": account.category, "action": "rollback", "status": "failed"})
    return rollback_succeeded


def rotate_local_mysql_credentials(
    *,
    settings: RotationSettings,
    backend: CredentialBackend,
    env_path: Path,
    report_path: Path,
    generated: dict[str, str] | None = None,
    dry_run: bool = False,
    env_writer: Callable[[Path, dict[str, str]], None] = atomic_update_env,
) -> dict[str, object]:
    settings.validate()
    generated = dict(generated or generate_credentials())
    if set(generated) != set(ROTATION_ORDER) or len(set(generated.values())) != len(ROTATION_ORDER):
        raise ValueError("generated database credentials are incomplete or duplicated")
    if any(len(value) < 32 or "\r" in value or "\n" in value for value in generated.values()):
        raise ValueError("generated database credentials do not meet policy")

    report: dict[str, object] = {
        "format": "query-pilot-mysql-credential-rotation",
        "version": 1,
        "started_at_utc": _utc_now(),
        "status": "dry_run" if dry_run else "running",
        "dry_run": dry_run,
        "steps": [],
    }
    steps = report["steps"]
    assert isinstance(steps, list)
    if dry_run:
        for account in settings.accounts:
            steps.append({"category": account.category, "action": "validate", "status": "passed"})
        report["completed_at_utc"] = _utc_now()
        _write_report(report_path, report)
        return report

    completed: list[AccountCredential] = []
    current_account: AccountCredential | None = None
    try:
        for account in settings.accounts:
            current_account = account
            backend.change_password(account, account.password, generated[account.category])
            completed.append(account)
            if not backend.verify(account, generated[account.category]):
                raise RuntimeError("new database credential verification failed")
            steps.append({"category": account.category, "action": "rotate", "status": "passed"})
    except Exception as exc:
        steps.append(
            {
                "category": current_account.category if current_account is not None else "database",
                "action": "rotate",
                "status": "failed",
            }
        )
        rolled_back = _rollback(
            backend=backend,
            completed=completed,
            generated=generated,
            steps=steps,
        )
        report["status"] = "rolled_back" if rolled_back else "rollback_failed"
        report["completed_at_utc"] = _utc_now()
        _write_report(report_path, report)
        message = "database rotation failed and was rolled back" if rolled_back else "database rotation failed and rollback was incomplete"
        raise CredentialRotationError(message) from exc

    try:
        env_writer(
            env_path,
            {account.env_key: generated[account.category] for account in settings.accounts},
        )
        steps.append({"category": "environment", "action": "update", "status": "passed"})
    except Exception as exc:
        steps.append({"category": "environment", "action": "update", "status": "failed"})
        rolled_back = _rollback(
            backend=backend,
            completed=completed,
            generated=generated,
            steps=steps,
        )
        report["status"] = "rolled_back" if rolled_back else "rollback_failed"
        report["completed_at_utc"] = _utc_now()
        _write_report(report_path, report)
        message = "environment update failed and database changes were rolled back" if rolled_back else "environment update failed and database rollback was incomplete"
        raise CredentialRotationError(message) from exc

    report["status"] = "succeeded"
    report["completed_at_utc"] = _utc_now()
    _write_report(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely rotate Query Pilot local MySQL credentials")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("artifacts/mysql-credential-rotation.json"),
    )
    parser.add_argument(
        "--backup-manifest",
        type=Path,
        help="verified backup manifest required by --apply",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            if args.backup_manifest is None:
                raise ValueError("--apply requires --backup-manifest")
            verify_backup_evidence(args.backup_manifest)
        settings = load_rotation_settings(args.env)
        backend = PyMySqlCredentialBackend(settings)
        report = rotate_local_mysql_credentials(
            settings=settings,
            backend=backend,
            env_path=args.env,
            report_path=args.report,
            dry_run=args.dry_run,
        )
        print(f"MySQL credential rotation {report['status']}; report: {args.report}")
        return 0
    except (CredentialRotationError, OSError, ValueError) as exc:
        print(f"MySQL credential rotation failed: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
