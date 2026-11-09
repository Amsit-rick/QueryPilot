from app.schemas.queries import QueryRequest


def test_query_request_contract_preserves_api_defaults():
    request = QueryRequest(question="查询销售额")
    assert request.params == {}
    assert request.offset == 0
    assert request.require_approval is False
