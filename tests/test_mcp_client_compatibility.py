import json
import subprocess
import sys
from pathlib import Path

import anyio

from app.mcp_client_compatibility import (
    CompatibilityCheck,
    TransportVerification,
    build_compatibility_matrix,
    verify_client_cancellation,
)


PROJECT_ROOT = Path(__file__).parents[1]


def test_matrix_only_marks_executed_official_sdk_transports_as_verified():
    matrix = build_compatibility_matrix(
        sdk_version="2.3.0",
        protocol_version="2025-11-25",
        verifications=(
            TransportVerification(
                client="official-python-sdk",
                client_version="2.3.0",
                transport="stdio",
                checks=(
                    CompatibilityCheck("initialize", "passed", "protocol negotiated"),
                    CompatibilityCheck("tools", "passed", "5 read-only tools"),
                ),
            ),
            TransportVerification(
                client="official-python-sdk",
                client_version="2.3.0",
                transport="streamable-http",
                checks=(
                    CompatibilityCheck("initialize", "passed", "protocol negotiated"),
                    CompatibilityCheck("tools", "passed", "5 read-only tools"),
                ),
            ),
        ),
    )

    verified = [row for row in matrix["clients"] if row["status"] == "verified"]
    assert {(row["client"], row["transport"]) for row in verified} == {
        ("official-python-sdk", "stdio"),
        ("official-python-sdk", "streamable-http"),
    }
    unverified = [row for row in matrix["clients"] if row["status"] == "not_tested"]
    assert {row["client"] for row in unverified} == {
        "claude-code",
        "claude-desktop",
        "cursor",
        "codex",
    }
    assert all(row["limitations"] for row in unverified)
    assert matrix["summary"] == {
        "verified": 2,
        "limited": 0,
        "failed": 0,
        "not_tested": 4,
    }


def test_matrix_downgrades_an_executed_transport_when_any_check_fails():
    matrix = build_compatibility_matrix(
        sdk_version="2.3.0",
        protocol_version="2025-11-25",
        verifications=(
            TransportVerification(
                client="official-python-sdk",
                client_version="2.3.0",
                transport="stdio",
                checks=(
                    CompatibilityCheck("initialize", "passed", "ok"),
                    CompatibilityCheck("cancellation", "failed", "request leaked"),
                ),
            ),
        ),
    )

    row = matrix["clients"][0]
    assert row["status"] == "failed"
    assert row["checks"]["cancellation"]["status"] == "failed"
    assert matrix["summary"]["failed"] == 1


def test_matrix_never_marks_an_executed_transport_without_checks_as_verified():
    matrix = build_compatibility_matrix(
        sdk_version="2.3.0",
        protocol_version="2025-11-25",
        verifications=(
            TransportVerification(
                client="official-python-sdk",
                client_version="2.3.0",
                transport="stdio",
                checks=(),
            ),
        ),
    )

    assert matrix["clients"][0]["status"] == "failed"


def test_matrix_marks_unexecuted_official_transport_as_not_tested():
    matrix = build_compatibility_matrix(
        sdk_version="2.3.0",
        protocol_version="2025-11-25",
        verifications=(
            TransportVerification(
                client="official-python-sdk",
                client_version="2.3.0",
                transport="stdio",
                checks=(CompatibilityCheck("initialize", "passed", "ok"),),
            ),
        ),
    )

    rows = {
        (row["client"], row["transport"]): row
        for row in matrix["clients"]
    }
    assert rows[("official-python-sdk", "stdio")]["status"] == "verified"
    assert rows[("official-python-sdk", "streamable-http")]["status"] == "not_tested"
    assert rows[("official-python-sdk", "streamable-http")]["limitations"]


def test_cancellation_probe_observes_cancelled_client_request():
    class BlockingSession:
        async def list_tools(self):
            await anyio.sleep_forever()

    result = anyio.run(verify_client_cancellation, BlockingSession())

    assert result.name == "cancellation"
    assert result.status == "passed"
    assert "client request cancelled" in result.detail


def test_compatibility_script_is_directly_executable():
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_mcp_client_compatibility.py"),
            "--help",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert "--output" in result.stdout
    assert "--transport" in result.stdout


def test_compatibility_script_replaces_stale_success_report_when_probe_fails(tmp_path):
    output = tmp_path / "compatibility.json"
    output.write_text('{"summary":{"verified":99}}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_mcp_client_compatibility.py"),
            "--transport",
            "stdio",
            "--python",
            str(tmp_path / "missing-python.exe"),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    rows = {
        (row["client"], row["transport"]): row
        for row in payload["clients"]
    }
    assert payload["summary"]["failed"] == 1
    assert rows[("official-python-sdk", "stdio")]["status"] == "failed"
    assert rows[("official-python-sdk", "streamable-http")]["status"] == "not_tested"
    assert "missing-python.exe" not in json.dumps(payload)


def test_checked_in_matrix_is_machine_readable_and_does_not_overclaim_clients():
    payload = json.loads(
        (PROJECT_ROOT / "data" / "mcp-client-compatibility.json").read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == 1
    assert payload["sdk"]["name"] == "mcp"
    assert payload["sdk"]["version"] == "2.3.0"
    assert payload["summary"]["verified"] >= 2
    for client in payload["clients"]:
        if client["client"] in {"claude-desktop", "cursor", "codex"}:
            assert client["status"] == "not_tested"
            assert client["limitations"]
