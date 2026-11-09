import pytest
from pydantic import ValidationError

from app.agent.contracts import AnalysisPlan, SqlPlan, merge_sql_params


def test_analysis_plan_accepts_only_canonical_controlled_paths():
    plan = AnalysisPlan(
        objective="describe sales",
        steps=["generate_sql", "validate_sql", "execute_query", "verify_result", "analyze_result"],
        requires_multiple_steps=True,
        analysis_mode="descriptive_statistics",
    )
    assert plan.analysis_mode == "descriptive_statistics"


@pytest.mark.parametrize("mode", ["trend", "period_comparison", "anomaly_detection", "auto_insights"])
def test_analysis_plan_accepts_new_controlled_modes(mode):
    plan = AnalysisPlan(
        objective="analyze time series",
        steps=["generate_sql", "validate_sql", "execute_query", "verify_result", "analyze_result"],
        requires_multiple_steps=True,
        analysis_mode=mode,
    )
    assert plan.analysis_mode == mode


@pytest.mark.parametrize("payload", [
    {"steps": ["generate_sql", "analyze_result"], "requires_multiple_steps": True, "analysis_mode": "descriptive_statistics"},
    {"requires_multiple_steps": True, "analysis_mode": "none"},
    {"analysis_mode": "unknown"},
])
def test_analysis_plan_rejects_uncontrolled_or_inconsistent_paths(payload):
    with pytest.raises(ValidationError):
        AnalysisPlan(objective="test", **payload)


def test_sql_plan_accepts_only_flat_typed_parameters():
    plan = SqlPlan(sql="SELECT id FROM sales WHERE region = :region", params={"region": "华东", "active": True})
    assert plan.params == {"region": "华东", "active": True}
    with pytest.raises(ValidationError):
        SqlPlan(sql="SELECT id FROM sales", params={"nested": {"unsafe": True}})
    with pytest.raises(ValidationError):
        SqlPlan(sql="SELECT id FROM sales", params={"bad-name": "x"})


def test_generated_params_cannot_silently_override_caller_params():
    assert merge_sql_params({"region": "华东"}, {"year": 2026}) == {"year": 2026, "region": "华东"}
    with pytest.raises(ValueError, match="E_QUERY_PARAM_CONFLICT"):
        merge_sql_params({"region": "华东"}, {"region": "华南"})
