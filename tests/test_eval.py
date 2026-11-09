from app.eval import run_eval

def test_eval_dataset_passes():
    report = run_eval()
    assert report["dataset_version"] == "2026-09-15-v2"
    assert report["sections"]["golden"]["total"] >= 50
    assert report["sections"]["boundary"]["total"] >= 15
    assert report["sections"]["adversarial"]["total"] >= 10
    assert report["accuracy"] == 1
    assert report["metrics"]["execution"]["passed"] == 65
    assert report["metrics"]["model_generation"]["evaluated"] == 4
    assert report["metrics"]["business_correctness"]["passed"] == 7
