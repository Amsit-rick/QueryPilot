import json
from pathlib import Path

from scripts.evaluate_reflection_results import evaluate_result_dataset


def test_result_level_reflection_evaluation_executes_at_least_one_hundred_cases():
    report = evaluate_result_dataset(Path("data/reflection_result_eval.json"))

    assert report["format"] == "query-pilot-reflection-result-eval"
    assert report["dataset_version"] == "reflection-result-eval-v1"
    assert report["synthetic"] is True
    assert report["external_validation"] == "not_run"
    assert report["case_count"] >= 100
    assert report["family_count"] >= 6
    assert report["gate"]["passed"] is True
    assert report["metrics"]["reflection_action_accuracy"] >= 0.95
    assert report["metrics"]["final_result_accuracy"] >= 0.95
    assert report["metrics"]["security_permission_bypasses"] == 0


def test_result_level_evaluator_fails_gate_when_repair_result_is_wrong(tmp_path):
    dataset = json.loads(Path("data/reflection_result_eval.json").read_text(encoding="utf-8"))
    dataset["families"] = [
        {
            "id": "wrong-repair",
            "scenario": "top_n",
            "count": 1,
            "expected_action": "regenerate_sql",
            "candidate_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
            "repair_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY total_amount ASC LIMIT 1",
            "gold_sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY total_amount DESC LIMIT 1",
        }
    ]
    target = tmp_path / "bad.json"
    target.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    report = evaluate_result_dataset(target)

    assert report["metrics"]["final_result_accuracy"] == 0
    assert report["gate"]["passed"] is False

