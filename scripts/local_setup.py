"""Configure a fresh local checkout and bootstrap its first administrator."""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Callable

import yaml

# Direct script execution puts ``scripts`` on sys.path, not the project root
# where the application ``app`` package lives.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ENV_PATTERN = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
COMPOSE_REFERENCE_PATTERN = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<operator>:-|:\?)(?P<argument>.*))?\}$"
)


def _compose_environment(compose_path: Path) -> dict[str, str]:
    try:
        payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read local Compose file: {compose_path}") from exc
    services = payload.get("services") if isinstance(payload, dict) else None
    mysql = services.get("mysql") if isinstance(services, dict) else None
    environment = mysql.get("environment") if isinstance(mysql, dict) else None
    if not isinstance(environment, dict):
        raise ValueError("local Compose file does not define MySQL environment")
    result = {str(key): str(value).strip() for key, value in environment.items() if value is not None}
    required = {
        "MYSQL_DATABASE",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "MYSQL_READONLY_USER",
        "MYSQL_READONLY_PASSWORD",
    }
    missing = sorted(key for key in required if not result.get(key, "").strip())
    if missing:
        raise ValueError("local Compose MySQL environment is missing: " + ", ".join(missing))
    secret_references = {
        "MYSQL_PASSWORD": "MYSQL_PASSWORD",
        "MYSQL_ROOT_PASSWORD": "MYSQL_ROOT_PASSWORD",
        "MYSQL_READONLY_PASSWORD": "MYSQL_QUERY_PASSWORD",
    }
    for compose_key, env_key in secret_references.items():
        match = COMPOSE_REFERENCE_PATTERN.fullmatch(result[compose_key])
        if not match or match.group("name") != env_key or match.group("operator") != ":?":
            raise ValueError(f"local Compose {compose_key} must require ${{{env_key}:?...}}")

    def public_value(compose_key: str, env_key: str, fallback: str) -> str:
        value = result[compose_key]
        match = COMPOSE_REFERENCE_PATTERN.fullmatch(value)
        if not match:
            return value
        if match.group("name") != env_key:
            raise ValueError(f"local Compose {compose_key} must reference {env_key}")
        if match.group("operator") == ":-" and match.group("argument"):
            return match.group("argument")
        return fallback

    return {
        "MYSQL_DATABASE": public_value("MYSQL_DATABASE", "MYSQL_DATABASE", "query_agent"),
        "MYSQL_USER": public_value("MYSQL_USER", "MYSQL_USER", "query_app"),
        "MYSQL_READONLY_USER": public_value("MYSQL_READONLY_USER", "MYSQL_QUERY_USER", "query_readonly"),
    }


def _env_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        match = ENV_PATTERN.match(line)
        if match:
            values[match.group("key")] = match.group("value")
    return values


def _replace_env(text: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    lines = []
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


def _local_secret(existing: dict[str, str], key: str, used: set[str]) -> str:
    current = existing.get(key, "").strip()
    if current:
        used.add(current)
        return current
    while True:
        candidate = secrets.token_urlsafe(32)
        if candidate not in used:
            used.add(candidate)
            return candidate


def configure_local_env(*, template: Path, target: Path, compose: Path) -> dict[str, str]:
    if target.resolve() in {template.resolve(), compose.resolve()}:
        raise ValueError("local environment output must not overwrite an input file")
    if not template.is_file():
        raise ValueError(f"environment template is missing: {template}")
    source = target.read_text(encoding="utf-8") if target.is_file() else template.read_text(encoding="utf-8")
    existing = _env_values(source)
    compose_env = _compose_environment(compose)
    jwt_secret = existing.get("JWT_SECRET", "").strip() or secrets.token_urlsafe(48)
    used_database_secrets = {
        value.strip()
        for key in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "MYSQL_QUERY_PASSWORD")
        if (value := existing.get(key, "")).strip()
    }
    mysql_password = _local_secret(existing, "MYSQL_PASSWORD", used_database_secrets)
    mysql_root_password = _local_secret(existing, "MYSQL_ROOT_PASSWORD", used_database_secrets)
    mysql_query_password = _local_secret(existing, "MYSQL_QUERY_PASSWORD", used_database_secrets)
    updates = {
        "DATABASE_BACKEND": "mysql",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "3307",
        "MYSQL_DATABASE": compose_env["MYSQL_DATABASE"],
        "MYSQL_USER": compose_env["MYSQL_USER"],
        "MYSQL_PASSWORD": mysql_password,
        "MYSQL_ROOT_PASSWORD": mysql_root_password,
        "MYSQL_QUERY_USER": compose_env["MYSQL_READONLY_USER"],
        "MYSQL_QUERY_PASSWORD": mysql_query_password,
        "JWT_SECRET": jwt_secret,
        # The delivery bundle exercises the real authenticated admin UI.
        # bootstrap-admin creates the local admin account before the API starts.
        # The administrator gets its own generated secret instead of reusing a
        # database password, so rotating one never breaks the other.
        "LOCAL_ADMIN_USERNAME": existing.get("LOCAL_ADMIN_USERNAME", "").strip() or "admin",
        "LOCAL_ADMIN_PASSWORD": _local_secret(existing, "LOCAL_ADMIN_PASSWORD", used_database_secrets),
        "AUTH_ENABLED": "true",
        "DEFAULT_DATASOURCE": "mysql-main",
        "AGENT_CHECKPOINT_ENABLED": "false",
    }
    rendered = _replace_env(source, updates)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return updates


def upgrade_query_credentials(*, target: Path, compose: Path) -> dict[str, str]:
    """Fill missing local DB credentials without breaking an existing data volume."""
    if not target.is_file():
        raise ValueError(f"local environment is missing: {target}")
    source = target.read_text(encoding="utf-8")
    existing = _env_values(source)
    compose_env = _compose_environment(compose)
    updates = {}
    if not existing.get("MYSQL_ROOT_PASSWORD", "").strip():
        legacy_control_password = existing.get("MYSQL_PASSWORD", "").strip()
        updates["MYSQL_ROOT_PASSWORD"] = legacy_control_password or _local_secret(existing, "MYSQL_ROOT_PASSWORD", set())
    if not existing.get("MYSQL_QUERY_USER", "").strip():
        updates["MYSQL_QUERY_USER"] = compose_env["MYSQL_READONLY_USER"]
    if not existing.get("MYSQL_QUERY_PASSWORD", ""):
        used = {
            value.strip()
            for key in ("MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD")
            if (value := (updates.get(key) or existing.get(key, ""))).strip()
        }
        updates["MYSQL_QUERY_PASSWORD"] = _local_secret(existing, "MYSQL_QUERY_PASSWORD", used)
    if not updates:
        return {}
    rendered = _replace_env(source, updates)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return updates


def bootstrap_admin(
    *,
    username: str,
    password: str,
    tenant_id: str = "default",
    reset_existing: bool = False,
    get_user_fn: Callable[[str], dict | None] | None = None,
    create_user_fn: Callable[[str, str, str, str], int] | None = None,
    update_password_fn: Callable[[int, str], None] | None = None,
    hash_password_fn: Callable[[str], str] | None = None,
) -> dict[str, object]:
    username = username.strip()
    tenant_id = tenant_id.strip()
    if not username or len(username) > 128:
        raise ValueError("administrator username must contain 1 to 128 characters")
    if not tenant_id or len(tenant_id) > 64:
        raise ValueError("tenant id must contain 1 to 64 characters")
    if len(password) < 8:
        raise ValueError("administrator password must contain at least 8 characters")
    if any(character in password for character in "\r\n"):
        raise ValueError("administrator password must be a single line")
    if any(function is None for function in (get_user_fn, create_user_fn, update_password_fn, hash_password_fn)):
        from app.identity import hash_password
        from app.identity_store import create_user, get_user, update_password

        get_user_fn = get_user_fn or get_user
        create_user_fn = create_user_fn or create_user
        update_password_fn = update_password_fn or update_password
        hash_password_fn = hash_password_fn or hash_password
    existing = get_user_fn(username)
    if existing:
        if reset_existing:
            update_password_fn(int(existing["id"]), hash_password_fn(password))
            return {"status": "reset", "user_id": int(existing["id"]), "username": username}
        return {"status": "existing", "user_id": int(existing["id"]), "username": username}
    user_id = create_user_fn(username, hash_password_fn(password), "admin", tenant_id)
    return {"status": "created", "user_id": int(user_id), "username": username}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a Query Pilot local test checkout")
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure = subparsers.add_parser("configure", help="create an aligned local .env")
    configure.add_argument("--template", type=Path, default=Path(".env.example"))
    configure.add_argument("--output", type=Path, default=Path(".env"))
    configure.add_argument("--compose", type=Path, default=Path("docker-compose.mysql.yml"))
    upgrade = subparsers.add_parser("upgrade-query-credentials", help="add missing local database credentials without replacing existing values")
    upgrade.add_argument("--output", type=Path, default=Path(".env"))
    upgrade.add_argument("--compose", type=Path, default=Path("docker-compose.mysql.yml"))
    bootstrap = subparsers.add_parser("bootstrap-admin", help="create the first local administrator")
    bootstrap.add_argument("--username", default=os.getenv("LOCAL_ADMIN_USERNAME", "admin"))
    bootstrap.add_argument("--tenant-id", default="default")
    bootstrap.add_argument("--reset-existing", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "configure":
            configure_local_env(template=args.template, target=args.output, compose=args.compose)
            print(f"Local environment configured: {args.output}")
            return 0
        if args.command == "upgrade-query-credentials":
            updates = upgrade_query_credentials(target=args.output, compose=args.compose)
            print("Local database credentials upgraded." if updates else "Local database credentials already configured.")
            return 0
        from app.config import settings

        env_file = Path(".env")
        file_values = _env_values(env_file.read_text(encoding="utf-8")) if env_file.is_file() else {}
        username = args.username or file_values.get("LOCAL_ADMIN_USERNAME", "").strip() or "admin"
        password = (
            os.getenv("LOCAL_ADMIN_PASSWORD", "").strip()
            or file_values.get("LOCAL_ADMIN_PASSWORD", "").strip()
            or settings.mysql_password
        )
        result = bootstrap_admin(
            username=username,
            password=password,
            tenant_id=args.tenant_id,
            reset_existing=args.reset_existing,
        )
        print(f"Local administrator {result['status']}: {result['username']} (id={result['user_id']})")
        return 0
    except Exception as exc:
        print(f"Local setup failed: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
