import json

import pytest

from app.policy import apply_policy, redact_rows, resolve_policy, _apply_mask
from app.guardrails import QueryError


def test_viewer_restricted():
    with pytest.raises(QueryError):
        apply_policy("SELECT amount FROM sales", {"roles": ["viewer"]})


def test_redaction():
    assert redact_rows(["name"], [["Alice"]], {"roles": ["analyst"]}) == [["***"]]


def test_viewer_table_forbidden():
    with pytest.raises(QueryError) as exc:
        apply_policy("SELECT id FROM customers", {"roles": ["viewer"]})
    assert exc.value.code == "E_TABLE_FORBIDDEN"


def test_viewer_star_forbidden():
    with pytest.raises(QueryError) as exc:
        apply_policy("SELECT * FROM sales", {"roles": ["viewer"]})
    assert exc.value.code == "E_COLUMN_FORBIDDEN"


def test_viewer_allowed_column_passes():
    assert apply_policy("SELECT region FROM sales", {"roles": ["viewer"]}) == "SELECT region FROM sales"


def test_analyst_has_full_column_access():
    assert apply_policy("SELECT amount FROM sales", {"roles": ["analyst"]}) == "SELECT amount FROM sales"


def test_admin_full_access_and_no_mask():
    assert apply_policy("SELECT * FROM customers", {"roles": ["admin"]}) == "SELECT * FROM customers"
    assert redact_rows(["name"], [["Alice"]], {"roles": ["admin"]}) == [["Alice"]]


def test_partial_mask_keeps_edges():
    assert _apply_mask("Alice", "partial") == "A***e"
    assert _apply_mask("ab", "partial") == "***"
    assert _apply_mask("Alice", "mask") == "***"


def test_unknown_role_denies_everything():
    policy = resolve_policy({"roles": ["nonexistent"]})
    assert policy["tables"] == []
    with pytest.raises(QueryError) as exc:
        apply_policy("SELECT region FROM sales", {"roles": ["nonexistent"]})
    assert exc.value.code == "E_TABLE_FORBIDDEN"


def test_policy_loaded_from_file(tmp_path, monkeypatch):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps({
        "version": "test",
        "datasources": {
            "default": {
                "roles": {
                    "viewer": {"tables": ["sales"], "columns": {"sales": ["region", "sold_at"]}, "mask": {}}
                }
            }
        },
    }), encoding="utf-8")
    monkeypatch.setattr("app.policy.settings.policy_path", str(policy_file))

    policy = resolve_policy({"roles": ["viewer"]})
    assert policy["tables"] == ["sales"]
    assert policy["columns"]["sales"] == ["region", "sold_at"]
    assert apply_policy("SELECT sold_at FROM sales", {"roles": ["viewer"]}) == "SELECT sold_at FROM sales"


def test_columns_are_checked_against_their_qualified_table(tmp_path, monkeypatch):
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(json.dumps({"datasources": {"default": {"roles": {
        "viewer": {"tables": ["sales", "customers"], "columns": {"sales": ["region"], "customers": ["name", "region"]}}
    }}}}), encoding="utf-8")
    monkeypatch.setattr("app.policy.settings.policy_path", str(policy_file))
    assert apply_policy("SELECT s.region, c.name FROM sales s JOIN customers c ON s.region = c.region", {"roles": ["viewer"]})
    with pytest.raises(QueryError) as exc:
        apply_policy("SELECT c.id FROM sales s JOIN customers c ON s.region = c.region", {"roles": ["viewer"]})
    assert exc.value.code == "E_COLUMN_FORBIDDEN"
