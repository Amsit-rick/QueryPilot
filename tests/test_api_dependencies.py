import pytest

from app.api.dependencies import require_query_access


def test_query_dependency_returns_authorized_identity():
    identity = {"subject": "7", "roles": ["analyst"]}
    assert require_query_access(identity) == identity


def test_query_dependency_rejects_unknown_role():
    with pytest.raises(Exception):
        require_query_access({"subject": "8", "roles": ["unknown"]})
