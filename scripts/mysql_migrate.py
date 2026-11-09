"""Apply pending MySQL migrations before the API starts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pymysql

# Direct script execution (used by Docker and the Windows setup command) puts
# ``scripts`` on sys.path, not the project root where the ``app`` package lives.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    statements: tuple[str, ...]


REPORT_FORMAT = "query-pilot-mysql-migration-plan"
REPORT_VERSION = 1


def _statements(path: Path) -> tuple[str, ...]:
    sql = "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")
    )
    return tuple(statement.strip() for statement in sql.split(";") if statement.strip())


def load_migrations(migrations_dir: Path) -> tuple[Migration, ...]:
    migrations = []
    for path in sorted(migrations_dir.glob("V*.sql")):
        version = path.name.split("__", 1)[0]
        # V002 is a DBA-only account grant and must never run as the
        # application user. It is handled by the MySQL init volume.
        if version == "V002":
            continue
        migrations.append(Migration(version, path, _statements(path)))
    return tuple(migrations)


def migration_plan(migrations_dir: Path, applied: set[str] | None = None) -> tuple[Migration, ...]:
    applied_versions = applied or set()
    return tuple(item for item in load_migrations(migrations_dir) if item.version not in applied_versions)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_dry_run_report(plan: tuple[Migration, ...]) -> dict:
    return {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database_connected": False,
        "database_state_checked": False,
        "planned_count": len(plan),
        "migrations": [
            {
                "version": migration.version,
                "file": migration.path.name,
                "size_bytes": migration.path.stat().st_size,
                "sha256": _sha256_file(migration.path),
                "statement_count": len(migration.statements),
            }
            for migration in plan
        ],
        "limitations": [
            "The dry-run does not connect to MySQL or read schema_migrations.",
            "All application migration files are listed; applied versions may still be skipped at runtime.",
        ],
    }


def write_dry_run_report(payload: dict, target: Path, migrations_dir: Path) -> None:
    protected = {path.resolve() for path in migrations_dir.glob("*") if path.is_file()}
    if target.resolve() in protected:
        raise ValueError("report path must not overwrite a migration file")
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


def mysql_connection_options() -> dict:
    """Use explicit process variables first, then the application's .env settings."""
    return {
        "host": os.getenv("MYSQL_HOST", settings.mysql_host),
        "port": int(os.getenv("MYSQL_PORT", str(settings.mysql_port))),
        "user": os.getenv("MYSQL_USER", settings.mysql_user),
        "password": os.getenv("MYSQL_PASSWORD", settings.mysql_password),
        "database": os.getenv("MYSQL_DATABASE", settings.mysql_database),
        "connect_timeout": 5,
        "autocommit": True,
    }


def _apply(migrations_dir: Path) -> None:
    files = load_migrations(migrations_dir)
    for attempt in range(30):
        try:
            conn = pymysql.connect(**mysql_connection_options())
            break
        except pymysql.MySQLError:
            if attempt == 29:
                raise
            time.sleep(2)
    with conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(32) PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            for migration in files:
                version = migration.version
                cur.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (version,))
                if cur.fetchone():
                    continue
                for statement in migration.statements:
                    try:
                        cur.execute(statement)
                    except pymysql.err.OperationalError as exc:
                        # Existing volumes may contain columns/tables created
                        # by an earlier deployment before migration tracking.
                        if exc.args and exc.args[0] in {1050, 1060, 1061}:
                            print(f"already present in {version}: {exc}")
                            continue
                        raise
                cur.execute("INSERT INTO schema_migrations(version) VALUES(%s)", (version,))
                print(f"applied {version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply or preview Query Pilot MySQL migrations")
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "migrations" / "mysql",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the ordered application plan without connecting to MySQL",
    )
    parser.add_argument("--report", type=Path, help="write a machine-readable dry-run report")
    args = parser.parse_args(argv)
    if args.report and not args.dry_run:
        parser.error("--report requires --dry-run")
    if args.dry_run:
        plan = migration_plan(args.migrations_dir)
        if args.report:
            write_dry_run_report(build_dry_run_report(plan), args.report, args.migrations_dir)
        if not plan:
            print("No application migration files found.")
            if args.report:
                print(f"Migration dry-run report written: {args.report}")
            return 0
        print("MySQL migration dry-run (no database connection):")
        for migration in plan:
            print(f"{migration.version}: {migration.path.name} ({len(migration.statements)} statements)")
        if args.report:
            print(f"Migration dry-run report written: {args.report}")
        return 0
    _apply(args.migrations_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
