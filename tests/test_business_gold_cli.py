import json

from app.config import settings
from scripts.evaluate_business_gold import main


def test_business_gold_cli_writes_local_report_and_can_require_external(tmp_path, monkeypatch):
    dataset = tmp_path / "gold.json"
    dataset.write_text(json.dumps({
        "format": "query-pilot-business-gold-dataset",
        "dataset_version": "business-gold-cli-test-v1",
        "synthetic": True,
        "cases": [{
            "id": "regional-sales",
            "category": "aggregation",
            "question": "各区域销售额",
            "gold_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY region",
            "expected_columns": ["region", "total_amount"],
            "expected_rows": [["华东", 1960.0], ["华南", 980.0]],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "model_api_key", "")

    output = tmp_path / "report.json"
    assert main(["--dataset", str(dataset), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["local_gate"]["passed"] is True
    assert report["external_validation"] == "not_run"

    strict = tmp_path / "strict.json"
    assert main([
        "--dataset", str(dataset),
        "--output", str(strict),
        "--require-external",
    ]) == 3
