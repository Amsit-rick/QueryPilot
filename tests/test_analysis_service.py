from decimal import Decimal

from app.agent.contracts import AnalysisMode
from app.services.analysis_service import describe_result, infer_analysis_mode


def test_describe_result_uses_only_supplied_rows():
    analysis = describe_result(
        (["name", "amount"], [["***", 10.0], ["***", 20.0]], False),
        {"analysis_mode": "descriptive_statistics"},
    )
    assert analysis.metrics == {"amount": {"count": 2, "min": 10.0, "max": 20.0, "average": 15.0}}


def test_describe_result_supports_database_decimal_values():
    analysis = describe_result(
        (["total"], [[Decimal("10.25")], [Decimal("20.75")]], False),
        {"analysis_mode": "descriptive_statistics"},
    )
    assert analysis.metrics["total"] == {"count": 2, "min": 10.25, "max": 20.75, "average": 15.5}


def test_infer_analysis_mode_uses_bounded_keywords():
    assert infer_analysis_mode("查看销售趋势") == AnalysisMode.TREND
    assert infer_analysis_mode("计算同比和环比") == AnalysisMode.PERIOD_COMPARISON
    assert infer_analysis_mode("识别异常值") == AnalysisMode.ANOMALY_DETECTION
    assert infer_analysis_mode("趋势和异常综合分析") == AnalysisMode.AUTO_INSIGHTS
    assert infer_analysis_mode("查询销售额") == AnalysisMode.NONE


def test_trend_analysis_sorts_time_and_reports_zero_baseline_safely():
    analysis = describe_result(
        (["month", "amount"], [["2026-02", 30], ["2026-01", 0], ["2026-03", 45]], False),
        {"analysis_mode": "auto_insights"},
    )
    assert analysis.time_column == "month"
    assert analysis.trends[0].model_dump() == {
        "column": "amount", "time_column": "month", "start_period": "2026-01",
        "end_period": "2026-03", "start_value": 0.0, "end_value": 45.0,
        "absolute_change": 45.0, "percent_change": None, "direction": "up", "points": 3,
    }
    assert analysis.comparisons[0].kind == "previous_period"
    assert analysis.comparisons[0].percent_change == 50.0
    assert "上一年" in "".join(analysis.limitations)


def test_period_comparison_generates_previous_period_and_year_over_year():
    analysis = describe_result(
        (["month", "revenue"], [["2025-02-01", 80], ["2026-01-01", 100], ["2026-02-01", 120]], False),
        {"analysis_mode": "period_comparison"},
    )
    assert [(item.kind, item.percent_change) for item in analysis.comparisons] == [
        ("previous_period", 20.0),
        ("year_over_year", 50.0),
    ]


def test_anomaly_analysis_uses_robust_baseline_and_caps_conclusions():
    rows = [[f"2026-{month:02d}", value] for month, value in enumerate([10, 10, 10, 100, 10], start=1)]
    analysis = describe_result(
        (["month", "amount"], rows, False),
        {"analysis_mode": "anomaly_detection"},
    )
    assert [item.model_dump() for item in analysis.anomalies] == [{
        "column": "amount", "period": "2026-04", "value": 100.0,
        "baseline": 10.0, "score": 18.0, "method": "median_deviation",
    }]
    assert "候选异常点" in analysis.summary


def test_time_analysis_refuses_duplicate_periods_and_marks_truncation():
    analysis = describe_result(
        (["day", "amount"], [["2026-01-01", 10], ["2026-01-01", 20], ["2026-01-02", 30]], True),
        {"analysis_mode": "trend"},
    )
    assert analysis.trends == []
    assert any("同一时间点" in item for item in analysis.limitations)
    assert any("截断" in item for item in analysis.limitations)


def test_time_analysis_requires_temporal_dimension():
    analysis = describe_result(
        (["region", "amount"], [["east", 10], ["west", 20]], False),
        {"analysis_mode": "trend"},
    )
    assert analysis.trends == []
    assert analysis.time_column is None
    assert "缺少可用时间序列" in analysis.summary
