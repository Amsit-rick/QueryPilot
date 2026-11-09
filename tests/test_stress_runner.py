from scripts.stress_runner import RequestResult, summarize_results


def test_stress_report_contains_percentiles_error_classes_and_gate():
    results = [
        RequestResult(duration_ms=10, ok=True, status="200"),
        RequestResult(duration_ms=20, ok=True, status="200"),
        RequestResult(duration_ms=30, ok=True, status="200"),
        RequestResult(duration_ms=40, ok=False, status="503", error_class="http_5xx"),
    ]

    report = summarize_results(
        results,
        workers=2,
        elapsed_seconds=0.1,
        min_success_rate=0.7,
        p95_max_ms=100,
        p99_max_ms=100,
    )

    assert report["format"] == "query-pilot-capacity-report"
    assert report["metrics"]["p50_ms"] == 20
    assert report["metrics"]["p95_ms"] == 40
    assert report["metrics"]["p99_ms"] == 40
    assert report["errors"] == {"http_5xx": 1}
    assert report["gate"]["passed"] is True


def test_stress_gate_fails_on_success_rate_or_latency_regression():
    results = [
        RequestResult(duration_ms=50, ok=True, status="200"),
        RequestResult(duration_ms=900, ok=False, status="timeout", error_class="timeout"),
    ]
    report = summarize_results(
        results,
        workers=1,
        elapsed_seconds=1,
        min_success_rate=0.99,
        p95_max_ms=500,
        p99_max_ms=500,
    )

    assert report["gate"]["passed"] is False
    assert set(report["gate"]["reason_codes"]) == {"SUCCESS_RATE_BELOW_THRESHOLD", "P95_LATENCY_EXCEEDED", "P99_LATENCY_EXCEEDED"}
