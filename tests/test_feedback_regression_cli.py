import json

from app.config import settings
from scripts.evaluate_feedback_regression import main


def test_feedback_regression_cli_records_not_run_and_can_require_external(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps({
        "format": "query-pilot-feedback-eval-dataset",
        "version": 1,
        "dataset_version": "feedback-regression-test-v1",
        "cases": [{
            "feedback_id": "case-1",
            "review_status": "approved",
            "question": "按区域汇总销售额",
            "datasource_id": "sqlite-demo",
            "expected_sql_template": "SELECT region, SUM(amount) FROM sales GROUP BY region",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "model_api_key", "")

    report_path = tmp_path / "report.json"
    assert main(["--dataset", str(dataset), "--output", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["external_validation"] == "not_run"
    assert report["gate"]["passed"] is False
    assert "generated_sql" not in report_path.read_text(encoding="utf-8")

    strict_path = tmp_path / "strict-report.json"
    assert main([
        "--dataset", str(dataset),
        "--output", str(strict_path),
        "--require-external",
    ]) == 3
