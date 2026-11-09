import pytest
from app.guardrails import QueryError, bound_query_rows, validate_sql

SCHEMA = {"sales": {"id", "region", "amount", "sold_at"}}
JOIN_SCHEMA = {"sales": {"id", "region", "amount", "sold_at"}, "customers": {"id", "name", "region"}}


def test_accepts_single_select():
    assert validate_sql("SELECT region, SUM(amount) FROM sales GROUP BY region", SCHEMA).startswith("SELECT")


@pytest.mark.parametrize("sql", ["DELETE FROM sales", "SELECT 1; DROP TABLE sales", "PRAGMA table_info(sales)"])
def test_rejects_mutation_or_multiple_statements(sql):
    with pytest.raises(QueryError) as exc:
        validate_sql(sql, SCHEMA)
    assert exc.value.code == "E_READONLY_VIOLATION"


def test_rejects_unknown_table():
    with pytest.raises(QueryError) as exc:
        validate_sql("SELECT * FROM customers", SCHEMA)
    assert exc.value.code == "E_HALLUCINATED_FIELD"


@pytest.mark.parametrize(("sql", "code"), [
    ("SELECT * FROM sales JOIN customers", "E_CARTESIAN_JOIN"),
    ("SELECT region FROM sales JOIN customers ON sales.region = customers.region", "E_AMBIGUOUS_FIELD"),
    ("SELECT missing FROM sales", "E_HALLUCINATED_FIELD"),
    ("SELECT * FROM secret_table", "E_HALLUCINATED_FIELD"),
    ("SELECT readfile('x') FROM sales", "E_READONLY_VIOLATION"),
    ("SELECT " + "x" * 5001 + " FROM sales", "E_QUERY_LIMIT"),
])
def test_adversarial_sql_is_rejected(sql, code):
    with pytest.raises(QueryError) as exc:
        validate_sql(sql, JOIN_SCHEMA, max_length=5000)
    assert exc.value.code == code


def test_join_with_alias_and_on_is_allowed():
    sql = "SELECT s.region, c.name FROM sales AS s JOIN customers AS c ON s.region = c.region"
    assert validate_sql(sql, JOIN_SCHEMA) == sql


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_boolean_predicates_are_not_treated_as_callable_functions(dialect):
    sql = "SELECT id FROM sales WHERE amount >= 100 AND region = '华东' OR id = 3"
    assert validate_sql(sql, SCHEMA, dialect=dialect) == sql


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_boolean_predicates_do_not_bypass_unknown_function_blocklist(dialect):
    sql = "SELECT id FROM sales WHERE amount > 0 AND dangerous_extension(amount) > 0"
    with pytest.raises(QueryError) as exc:
        validate_sql(sql, SCHEMA, dialect=dialect)
    assert exc.value.code == "E_READONLY_VIOLATION"


def test_select_alias_is_allowed_in_order_by():
    sql = "SELECT region, SUM(amount) AS total_amount FROM sales GROUP BY region ORDER BY total_amount DESC"
    assert validate_sql(sql, JOIN_SCHEMA) == sql


def test_case_when_is_allowed_in_readonly_select():
    sql = "SELECT id, CASE WHEN amount >= 10000 THEN 'high' ELSE 'normal' END AS sale_level FROM sales"
    assert validate_sql(sql, SCHEMA) == sql


def test_non_recursive_ctes_can_preaggregate_multiple_fact_tables():
    sql = (
        "WITH item_totals AS ("
        "SELECT oi.order_id, SUM(oi.quantity * oi.unit_price) AS item_amount "
        "FROM order_items oi GROUP BY oi.order_id"
        "), payment_totals AS ("
        "SELECT p.order_id, SUM(p.amount) AS paid_amount "
        "FROM payments p GROUP BY p.order_id"
        ") "
        "SELECT o.id, i.item_amount, p.paid_amount FROM orders o "
        "JOIN item_totals i ON i.order_id = o.id "
        "JOIN payment_totals p ON p.order_id = o.id"
    )
    schema = {
        "orders": {"id"},
        "order_items": {"order_id", "quantity", "unit_price"},
        "payments": {"order_id", "amount"},
    }

    assert validate_sql(sql, schema) == sql


def test_recursive_ctes_remain_blocked():
    sql = "WITH RECURSIVE filtered AS (SELECT s.id FROM sales s) SELECT f.id FROM filtered f"

    with pytest.raises(QueryError) as exc:
        validate_sql(sql, SCHEMA)

    assert exc.value.code == "E_READONLY_VIOLATION"


@pytest.mark.parametrize(("dialect", "sql"), [
    ("mysql", "SELECT SLEEP(10) FROM sales"),
    ("mysql", "SELECT LOAD_FILE('/etc/passwd') FROM sales"),
    ("mysql", "SELECT * FROM sales FOR UPDATE"),
    ("postgres", "SELECT pg_read_file('/etc/passwd') FROM sales"),
    ("postgres", "SELECT * FROM sales FOR UPDATE"),
    ("mysql", "SELECT region FROM sales UNION SELECT region FROM sales"),
])
def test_dialect_specific_unsafe_selects_are_rejected(dialect, sql):
    with pytest.raises(QueryError) as exc:
        validate_sql(sql, SCHEMA, dialect=dialect)
    assert exc.value.code == "E_READONLY_VIOLATION"


def test_unknown_function_is_denied_by_default():
    with pytest.raises(QueryError, match="函数未在安全白名单"):
        validate_sql("SELECT dangerous_extension(amount) FROM sales", SCHEMA, dialect="mysql")


def test_named_parameter_count_is_bounded():
    predicates = " OR ".join(f"id=:value{index}" for index in range(3))
    with pytest.raises(QueryError) as exc:
        validate_sql(f"SELECT id FROM sales WHERE {predicates}", SCHEMA, max_params=2)
    assert exc.value.code == "E_QUERY_LIMIT"


@pytest.mark.parametrize(("dialect", "sql"), [
    ("mysql", "SELECT DATE_FORMAT(sold_at, '%Y-%m') AS month, SUM(amount) FROM sales GROUP BY month"),
    ("postgres", "SELECT DATE(sold_at), AVG(amount) FROM sales GROUP BY DATE(sold_at)"),
    ("mysql", "SELECT MONTH(sold_at) AS month, COUNT(*) FROM sales GROUP BY MONTH(sold_at)"),
    ("sqlite", "SELECT MONTH(sold_at) AS month, COUNT(*) FROM sales GROUP BY MONTH(sold_at)"),
    ("mysql", "SELECT CASE WHEN amount < 10 THEN 'low' ELSE 'high' END AS band FROM sales ORDER BY FIELD(band, 'low', 'high')"),
])
def test_common_analytics_functions_remain_allowed(dialect, sql):
    assert validate_sql(sql, SCHEMA, dialect=dialect) == sql


@pytest.mark.parametrize("dialect", ["sqlite", "mysql", "postgres"])
def test_execution_bound_is_added_without_expanding_existing_limit(dialect):
    assert bound_query_rows("SELECT id FROM sales", dialect=dialect, max_rows=10) .endswith("LIMIT 11")
    bounded = bound_query_rows("SELECT id FROM sales LIMIT 5", dialect=dialect, max_rows=10)
    assert bounded.endswith("LIMIT 5")


def test_execution_bound_accounts_for_external_offset_and_caps_large_limit():
    bounded = bound_query_rows("SELECT id FROM sales LIMIT 999", dialect="mysql", max_rows=10, offset=4)
    assert bounded.endswith("LIMIT 15")


@pytest.mark.parametrize("sql", ["SELECT id FROM sales LIMIT :size", "SELECT id FROM sales LIMIT -1"])
def test_dynamic_or_negative_limit_is_rejected(sql):
    with pytest.raises(QueryError) as exc:
        bound_query_rows(sql, dialect="mysql", max_rows=10)
    assert exc.value.code == "E_QUERY_LIMIT"
