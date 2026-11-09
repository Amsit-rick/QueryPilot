import json
import subprocess

from scripts.verify_local_bundle import verify_bundle


def _ready_project(tmp_path):
    for relative in (
        ".env",
        ".venv/Scripts/python.exe",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/node_modules",
        "docker-compose.mysql.yml",
        "migrations/mysql",
        "scripts/start_local.cmd",
    ):
        path = tmp_path / relative
        path.mkdir(parents=True, exist_ok=True) if "." not in path.name else path.parent.mkdir(parents=True, exist_ok=True)
        if "." in path.name:
            path.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "DATABASE_BACKEND=mysql\nMYSQL_HOST=127.0.0.1\nMYSQL_PORT=3307\nMYSQL_DATABASE=query_agent\n"
        "MYSQL_USER=query_app\nMYSQL_PASSWORD=do-not-print\nMYSQL_ROOT_PASSWORD=root-secret\n"
        "MYSQL_QUERY_USER=query_readonly\nMYSQL_QUERY_PASSWORD=query-secret\n",
        encoding="utf-8",
    )


def _runner(command, cwd):
    if command[0] in {"node", "npm", "npm.cmd"}:
        output = "v22.0.0" if command[0] == "node" else "10.0.0"
        return subprocess.CompletedProcess(command, 0, output, "")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_preflight_reports_ready_bundle_without_secret_value(tmp_path):
    _ready_project(tmp_path)
    report = verify_bundle(tmp_path, runner=_runner, port_checker=lambda port: port == 3307, which=lambda name: name)

    assert report["blocking"] == []
    assert report["secrets_redacted"] is True
    rendered = json.dumps(report, ensure_ascii=False)
    assert "do-not-print" not in rendered


def test_preflight_blocks_missing_password_and_non_mysql_backend(tmp_path):
    _ready_project(tmp_path)
    (tmp_path / ".env").write_text("DATABASE_BACKEND=sqlite\nMYSQL_PORT=3307\n", encoding="utf-8")
    report = verify_bundle(tmp_path, runner=_runner, port_checker=lambda _: False, which=lambda name: name)

    assert any("MYSQL_PASSWORD" in item for item in report["blocking"])
    assert any("DATABASE_BACKEND" in item for item in report["blocking"])


def test_preflight_warns_when_local_database_passwords_are_reused(tmp_path):
    _ready_project(tmp_path)
    env_path = tmp_path / ".env"
    content = env_path.read_text(encoding="utf-8")
    content = content.replace("do-not-print", "shared-secret").replace("root-secret", "shared-secret").replace("query-secret", "shared-secret")
    env_path.write_text(content, encoding="utf-8")

    report = verify_bundle(tmp_path, runner=_runner, port_checker=lambda port: port == 3307, which=lambda name: name)

    assert report["blocking"] == []
    assert any("复用密码" in item for item in report["warnings"])


def test_preflight_blocks_docker_and_api_port_failures(tmp_path):
    _ready_project(tmp_path)

    def runner(command, cwd):
        if command[1:] == ["info"]:
            return subprocess.CompletedProcess(command, 1, "", "offline")
        if command[0] in {"node", "npm", "npm.cmd"}:
            output = "v22.0.0" if command[0] == "node" else "10.0.0"
            return subprocess.CompletedProcess(command, 0, output, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    report = verify_bundle(tmp_path, runner=runner, port_checker=lambda port: port in {3307, 8000}, which=lambda name: name)

    assert any("Docker Desktop" in item for item in report["blocking"])
    assert any("端口 8000" in item for item in report["blocking"])


def test_preflight_is_machine_readable_and_detects_missing_bundle_file(tmp_path):
    _ready_project(tmp_path)
    (tmp_path / "frontend/package-lock.json").unlink()
    report = verify_bundle(tmp_path, runner=_runner, port_checker=lambda _: False, which=lambda name: name)

    assert report["format"] == "query-pilot-local-bundle-preflight"
    assert any("frontend/package-lock.json" in item for item in report["blocking"])


def test_preflight_blocks_unsupported_node_and_npm_versions(tmp_path):
    _ready_project(tmp_path)

    def runner(command, cwd):
        if command[0] == "node":
            return subprocess.CompletedProcess(command, 0, "v20.0.0", "")
        if command[0] in {"npm", "npm.cmd"}:
            return subprocess.CompletedProcess(command, 0, "9.0.0", "")
        return _runner(command, cwd)

    report = verify_bundle(tmp_path, runner=runner, port_checker=lambda _: False, which=lambda name: name)

    assert any("Node.js 版本过低" in item for item in report["blocking"])
    assert any("npm 版本过低" in item for item in report["blocking"])
