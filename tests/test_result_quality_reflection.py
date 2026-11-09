from app.agent.reflection import ReflectionAction, ReflectionFailureType
from app.services.result_quality_service import ResultQualityService


def _assess(**overrides):
    payload = {
        "question": "按区域统计销售额",
        "sql": "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
        "columns": ["region", "total_amount"],
        "rows": [["华东", 120], ["华南", 80]],
        "truncated": False,
        "authorized_schema": {"sales": {"region", "amount", "created_at", "status"}},
        "relationships": [],
        "schema_details": {},
        "expected_dimensions": ["region"],
        "expected_metrics": ["total_amount"],
        "explain_summary": {"supported": True, "estimated_rows": 2},
    }
    payload.update(overrides)
    return ResultQualityService().assess(**payload)


def test_result_quality_accepts_correct_aggregate_without_exposing_values():
    assessment = _assess(rows=[["sensitive-region", 120]])

    assert assessment.outcome == "accepted"
    assert assessment.reflection.accepted is True
    assert assessment.safe_summary["row_count"] == 1
    assert "sensitive-region" not in str(assessment.safe_summary)


def test_result_quality_rejects_missing_requested_dimension_and_metric():
    assessment = _assess(
        sql="SELECT COUNT(*) AS order_count FROM sales",
        columns=["order_count"],
        rows=[[3]],
    )

    assert assessment.outcome == "rejected"
    assert set(assessment.reason_codes) >= {
        "EXPECTED_DIMENSION_MISSING",
        "EXPECTED_METRIC_MISSING",
    }
    assert assessment.reflection.failure_type == ReflectionFailureType.AGGREGATION_GRAIN
    assert assessment.reflection.action == ReflectionAction.REGENERATE_SQL


def test_result_quality_rejects_top_n_without_order_and_limit():
    assessment = _assess(
        question="销售额最高的前 10 个区域",
        sql="SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
    )

    assert assessment.outcome == "rejected"
    assert "TOP_N_ORDER_MISSING" in assessment.reason_codes
    assert "TOP_N_LIMIT_MISSING" in assessment.reason_codes


def test_result_quality_detects_one_to_many_aggregate_fanout():
    assessment = _assess(
        question="统计客户额度",
        sql="SELECT c.id, SUM(c.credit_limit) AS total_credit FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.id",
        columns=["id", "total_credit"],
        rows=[[1, 2000]],
        authorized_schema={
            "customers": {"id", "credit_limit"},
            "orders": {"id", "customer_id"},
        },
        relationships=[{
            "from_table": "orders",
            "from_columns": ["customer_id"],
            "to_table": "customers",
            "to_columns": ["id"],
            "cardinality": "many_to_one",
            "confidence": 1.0,
        }],
        expected_dimensions=["id"],
        expected_metrics=["total_credit"],
    )

    assert assessment.outcome == "rejected"
    assert "AGGREGATE_FANOUT_RISK" in assessment.reason_codes
    assert assessment.reflection.failure_type == ReflectionFailureType.AGGREGATION_GRAIN


def test_result_quality_detects_two_fact_tables_without_preaggregation():
    assessment = _assess(
        question="按客户统计订单和回款",
        sql=(
            "SELECT c.id, SUM(o.amount) AS order_amount, SUM(p.amount) AS paid_amount "
            "FROM customers c JOIN orders o ON o.customer_id=c.id "
            "JOIN payments p ON p.customer_id=c.id GROUP BY c.id"
        ),
        columns=["id", "order_amount", "paid_amount"],
        rows=[[1, 100, 80]],
        authorized_schema={
            "customers": {"id"},
            "orders": {"customer_id", "amount"},
            "payments": {"customer_id", "amount"},
        },
        relationships=[
            {"from_table": "orders", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"], "cardinality": "many_to_one", "confidence": 1.0},
            {"from_table": "payments", "from_columns": ["customer_id"], "to_table": "customers", "to_columns": ["id"], "cardinality": "many_to_one", "confidence": 1.0},
        ],
        expected_dimensions=["id"],
        expected_metrics=["order_amount", "paid_amount"],
    )

    assert assessment.outcome == "rejected"
    assert "DOUBLE_FACT_PREAGGREGATION_REQUIRED" in assessment.reason_codes


def test_result_quality_rejects_empty_result_but_only_warns_for_truncation():
    empty = _assess(rows=[])
    truncated = _assess(truncated=True)

    assert empty.outcome == "rejected"
    assert empty.reflection.failure_type == ReflectionFailureType.EMPTY_RESULT
    assert empty.reflection.action == ReflectionAction.REGENERATE_SQL
    assert truncated.outcome == "accepted"
    assert truncated.warning_codes == ["RESULT_TRUNCATED"]


def test_result_quality_detects_duplicate_requested_dimension():
    assessment = _assess(rows=[["华东", 120], ["华东", 80]])

    assert assessment.outcome == "rejected"
    assert "DUPLICATE_DIMENSION_GRAIN" in assessment.reason_codes


def test_result_quality_marks_business_scope_as_uncertain_for_critic():
    assessment = _assess(
        question="统计当前有效订单销售额",
        sql="SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region",
        schema_details={"tables": {"sales": {"columns": ["region", "amount", "status", "deleted_at"]}}},
    )

    assert assessment.outcome == "uncertain"
    assert set(assessment.reason_codes) >= {"STATUS_SCOPE_UNCERTAIN", "SOFT_DELETE_SCOPE_UNCERTAIN"}
    assert assessment.reflection.action == ReflectionAction.ASK_CLARIFICATION

