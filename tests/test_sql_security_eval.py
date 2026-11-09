from pathlib import Path

from scripts.evaluate_sql_security import evaluate


def test_versioned_sql_security_corpus_passes():
    report = evaluate(Path(__file__).parents[1] / "data" / "sql_security_eval.json")

    assert report["total"] >= 25
    assert report["passed"] == report["total"]
    assert report["corpus_accuracy"] == 1
    assert report["limitations"]
