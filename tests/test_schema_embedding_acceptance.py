from scripts.schema_embedding_acceptance import run_acceptance


def test_embedding_acceptance_passes_only_repeatable_bounded_scores(monkeypatch):
    monkeypatch.setattr(
        "scripts.schema_embedding_acceptance.schema_embedding_runtime_status",
        lambda: {"status": "ready", "configured": True},
    )

    report = run_acceptance(lambda _question, documents: {name: 0.75 for name in documents})

    assert report["external_validation"] == "passed"
    assert report["gate"]["passed"] is True
    assert "scores" not in report


def test_embedding_acceptance_redacts_provider_failure(monkeypatch):
    monkeypatch.setattr(
        "scripts.schema_embedding_acceptance.schema_embedding_runtime_status",
        lambda: {"status": "degraded", "last_error_code": "E_EMBEDDING_REQUEST_FAILED"},
    )

    def broken(_question, _documents):
        raise RuntimeError("secret=do-not-report upstream body")

    report = run_acceptance(broken)

    assert report["external_validation"] == "failed"
    assert report["gate"]["reason_codes"] == ["EMBEDDING_PROBE_FAILED"]
    assert "do-not-report" not in str(report)
