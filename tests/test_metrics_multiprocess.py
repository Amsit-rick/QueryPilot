import os
from pathlib import Path
import subprocess
import sys


def test_prometheus_multiprocess_registry_aggregates_worker_metrics(tmp_path):
    metrics_dir = tmp_path / "prometheus"
    metrics_dir.mkdir()
    project_root = Path(__file__).resolve().parents[1]
    script = """
from app.metrics_runtime import record_audit_event, render_metrics
record_audit_event({"duration_ms": 100, "row_count": 2})
print(render_metrics().decode("utf-8"))
"""
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(metrics_dir)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "query_pilot_requests_total 1.0" in completed.stdout
    assert "query_pilot_rows_returned_total 2.0" in completed.stdout
    assert "query_pilot_request_duration_seconds_count 1.0" in completed.stdout
