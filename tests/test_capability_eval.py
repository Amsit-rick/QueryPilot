from app.capability_eval import run_capability_eval


def test_cross_capability_evaluation_is_versioned_and_complete():
    report = run_capability_eval()

    assert report["dataset_version"] == "2026-11-01-v4"
    assert report["total"] == 24
    assert report["passed"] == 24
    assert report["accuracy"] == 1
    assert set(report["sections"]) == {
        "approval",
        "analysis",
        "chart",
        "connector",
        "follow_up",
        "gateway",
        "integration",
        "permission",
        "quality",
        "semantic",
        "value_grounding",
    }
    assert report["external_validation"] == "not_run"
