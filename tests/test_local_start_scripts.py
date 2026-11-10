import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_start_script_keeps_process_cleanup_non_destructive():
    content = _read("start_local.cmd").lower()

    assert "verify_local_bundle.py" in content
    assert "upgrade-query-credentials" in content
    assert "local_setup.py configure" not in content
    assert "scripts\\mysql_migrate.py" in content
    assert "zz99-create-readonly-user.sh" in content
    assert "dedicated mysql read-only query account" in content
    assert "database migrations failed" in content
    assert "docker compose -f docker-compose.mysql.yml exec -t mysql mysqladmin ping" in content
    assert "generated a temporary local metrics token" in content
    assert "metRICS_TOKEN".lower() in content
    assert "ps --status running -q mysql" in content
    assert "unknown mysql instance" in content
    assert "start_all_datasources.cmd" in content
    assert "start_all_datasources" in content
    assert "taskkill" not in content
    assert "stop-process" not in content
    assert "docker compose -f docker-compose.app.yml" not in content


def test_all_datasource_launcher_starts_known_instances_and_verifies_queries():
    content = _read("start_all_datasources.cmd").lower()

    assert "docker-compose.mysql.yml" in content
    assert "mysql did not become ready" in content
    assert "docker-compose.postgres.yml" in content
    assert "docker-compose.sqlserver.yml" in content
    assert "query-pilot-sqlserver" in content
    assert "provision_sqlserver.py" in content
    assert ".state.health.status" in content
    assert "sql server did not become healthy" in content
    assert "qp-clickhouse" in content
    assert "verify_configured_datasources.py" in content
    assert "--wait-seconds 120" in content
    assert "--remove-orphans" not in content
    assert "taskkill" not in content
    assert "stop-process" not in content


def test_setup_script_delegates_to_single_start_entrypoint():
    content = _read("setup_local.cmd").lower()

    assert "call scripts\\start_local.cmd" in content
    assert "bootstrap-admin" in content
    assert "load_enterprise_mysql.py --if-empty" in content
    assert "zz99-create-readonly-user.sh" in content
    assert "taskkill" not in content
    assert "stop-process" not in content


def test_local_scripts_wait_without_reading_from_stdin():
    for name in ("setup_local.cmd", "start_local.cmd"):
        content = _read(name).lower()

        assert "timeout /t" not in content
        assert "ping 127.0.0.1 -n 2 >nul" in content


def test_stack_check_uses_redacted_runtime_probe():
    content = _read("check_stack.cmd").lower()

    assert "verify_local_stack.py" in content
    assert "curl.exe" not in content
    assert "taskkill" not in content


def test_frontend_dev_server_uses_same_ipv4_address_as_stack_probe():
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["dev"] == "vite --host 127.0.0.1"


def test_mcp_stdio_launcher_reads_key_without_printing_it():
    content = _read("start_mcp_stdio.cmd")
    lowered = content.lower()

    assert "API_KEYS" in content
    assert "QUERY_PILOT_MCP_TOKEN" in content
    assert "run_mcp_server.py --transport stdio" in content
    assert "echo %QUERY_PILOT_MCP_TOKEN%" not in content
    assert "taskkill" not in lowered
    assert "stop-process" not in lowered
