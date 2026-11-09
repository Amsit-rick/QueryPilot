import json

from scripts import ci_verify


def _write_secure_project(root):
    frontend = root / "frontend"
    frontend.mkdir()
    package = {
        "dependencies": {"vue": "3.5.42"},
        "devDependencies": {"vite": "8.2.2"},
    }
    (frontend / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (frontend / "package-lock.json").write_text(
        json.dumps({"packages": {"": package}}), encoding="utf-8"
    )
    (root / "Dockerfile").write_text(
        "FROM python:3.12-slim\nWORKDIR /app\nUSER app\n", encoding="utf-8"
    )
    (root / ".dockerignore").write_text(
        ".git\n.env\n.env.*\n.venv\nartifacts\ntests\n", encoding="utf-8"
    )
    (root / "docker-compose.app.yml").write_text(
        """services:
  api:
    depends_on:
      redis:
        condition: service_healthy
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 256
    mem_limit: 1g
  export-worker:
    depends_on:
      redis:
        condition: service_healthy
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 128
    mem_limit: 512m
  mysql:
    image: mysql:8.4@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  redis:
    image: redis:7-alpine@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
    healthcheck:
      test: [\"CMD\", \"redis-cli\", \"ping\"]
volumes:
""",
        encoding="utf-8",
    )
    (root / "docker-compose.mysql.yml").write_text(
        """services:
  mysql:
    image: mysql:8.4@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    environment:
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:?MYSQL_PASSWORD must be set}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set}
      MYSQL_READONLY_PASSWORD: ${MYSQL_QUERY_PASSWORD:?MYSQL_QUERY_PASSWORD must be set}
""",
        encoding="utf-8",
    )


def test_supply_chain_gate_accepts_pinned_and_hardened_project(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is True
    assert "精确锁定" in result.detail


def test_supply_chain_gate_rejects_unbounded_frontend_version(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    package_path = tmp_path / "frontend" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["dependencies"]["vue"] = "latest"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "vue=latest" in result.detail


def test_supply_chain_gate_requires_frontend_manifest(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    (tmp_path / "frontend" / "package.json").unlink()
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "frontend/package.json 缺失" in result.detail


def test_supply_chain_gate_requires_sensitive_build_context_ignores(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    (tmp_path / ".dockerignore").write_text(".git\n", encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert ".dockerignore 缺少" in result.detail


def test_supply_chain_gate_rejects_runtime_database_password_defaults(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "backup_mysql.cmd").write_text(
        "if \"%MYSQL_ROOT_PASSWORD%\"==\"\" set MYSQL_ROOT_PASSWORD=dev-password\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "backup_mysql.cmd 不得包含数据库密码默认值" in result.detail


def test_supply_chain_gate_rejects_passwords_in_command_arguments(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "restore_mysql.cmd").write_text(
        "docker exec mysql mysqldump -p%MYSQL_ROOT_PASSWORD% query_agent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "restore_mysql.cmd 不得将数据库密码拼入命令参数" in result.detail


def test_supply_chain_gate_rejects_root_container(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "非 root" in result.detail


def test_supply_chain_gate_requires_redis_health_dependency(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    compose_path = tmp_path / "docker-compose.app.yml"
    compose_path.write_text(
        compose_path.read_text(encoding="utf-8").replace(
            "condition: service_healthy", "condition: service_started", 1
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "api 必须等待 redis service_healthy" in result.detail


def test_supply_chain_gate_requires_database_and_cache_digests(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    compose_path = tmp_path / "docker-compose.app.yml"
    compose_path.write_text(
        compose_path.read_text(encoding="utf-8").replace(
            "mysql:8.4@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "mysql:8.4",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "mysql 镜像必须固定 sha256 digest" in result.detail


def test_supply_chain_gate_rejects_hardcoded_local_mysql_credentials(tmp_path, monkeypatch):
    _write_secure_project(tmp_path)
    (tmp_path / "docker-compose.mysql.yml").write_text(
        """services:
  mysql:
    image: mysql:8.4
    environment:
      MYSQL_PASSWORD: fixed-password
      MYSQL_ROOT_PASSWORD: fixed-password
      MYSQL_READONLY_PASSWORD: fixed-password
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)

    result = ci_verify.check_supply_chain_config()

    assert result.passed is False
    assert "本地 MySQL Compose" in result.detail


def test_dependency_audit_marks_missing_tools_as_not_executed(tmp_path, monkeypatch):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ci_verify.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ci_verify.importlib.util, "find_spec", lambda _name: None)

    result = ci_verify.check_dependency_vulnerabilities()

    assert result.passed is True
    assert "未执行" in result.detail
    assert len(result.warnings) == 2
    assert result.executed is False
    assert result.blocking is False


def test_dependency_audit_can_be_required_for_release(tmp_path, monkeypatch):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ci_verify.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ci_verify.importlib.util, "find_spec", lambda _name: None)

    result = ci_verify.check_dependency_vulnerabilities(require=True)

    assert result.passed is False
    assert "未实际完成" in result.detail
    assert result.executed is False
    assert result.blocking is True


def test_dependency_audit_blocks_reported_vulnerabilities(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        ci_verify.shutil,
        "which",
        lambda name: name if name in {"pip-audit", "npm", "npm.cmd"} else None,
    )
    monkeypatch.setattr(
        ci_verify,
        "_run",
        lambda command, cwd=None, timeout=None: (
            (1, '[{"name":"x","vulns":[{"id":"PYSEC-1"}]}]')
            if "pip-audit" in command[0]
            else (1, '{"metadata":{"vulnerabilities":{"total":2}}}')
        ),
    )

    result = ci_verify.check_dependency_vulnerabilities()

    assert result.passed is False
    assert "Python 依赖发现 1" in result.detail
    assert "前端生产依赖发现 2" in result.detail
    assert result.executed is True
    assert result.blocking is True


def test_pip_audit_parser_accepts_current_object_format_with_preamble():
    output = (
        "WARNING:venv:Actual environment location may have moved\n"
        '{"dependencies":[{"name":"pytest","version":"9.1.1","vulns":[]},'
        '{"name":"example","version":"1.0","vulns":[{"id":"PYSEC-1"}]}],"fixes":[]}'
    )

    assert ci_verify._parse_pip_audit(output) == 1


def test_report_marks_not_executed_and_blocking_failures(tmp_path, monkeypatch):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ci_verify, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ci_verify.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ci_verify.importlib.util, "find_spec", lambda _name: None)

    result = ci_verify.check_dependency_vulnerabilities(require=True)
    payload = {"gates": [ci_verify.asdict(result)]}

    assert payload["gates"][0]["executed"] is False
    assert payload["gates"][0]["blocking"] is True
