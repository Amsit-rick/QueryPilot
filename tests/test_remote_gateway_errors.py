import pytest

from app.domain.data_gateway import GatewayError, GatewayQuery
from app.gateways.errors import classify_gateway_error
from app.gateways.sqlserver_gateway import SQLServerGateway


class DriverError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class NestedDriverError(Exception):
    def __init__(self, code):
        detail = type("DriverDetail", (), {"code": code})()
        super().__init__(detail)


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (TimeoutError("socket timed out at db.internal"), "E_GATEWAY_TIMEOUT", True),
        (DriverError(1017, "ORA-01017 invalid password secret"), "E_GATEWAY_AUTH", False),
        (DriverError(1031, "ORA-01031 insufficient privileges"), "E_GATEWAY_PERMISSION", False),
        (DriverError(12541, "ORA-12541 no listener db.internal"), "E_GATEWAY_CONNECTION", True),
        (DriverError(209, "ClickHouse network error db.internal"), "E_GATEWAY_CONNECTION", True),
        (NestedDriverError(1017), "E_GATEWAY_AUTH", False),
    ],
)
def test_driver_errors_are_classified_without_leaking_details(error, code, retryable):
    classified = classify_gateway_error(error, operation="query")

    assert classified.code == code
    assert classified.retryable is retryable
    assert "secret" not in str(classified)
    assert "db.internal" not in str(classified)


def test_query_uses_stable_timeout_error_code():
    def connect():
        raise TimeoutError("db.internal timed out with secret")

    gateway = SQLServerGateway(gateway_id="sqlserver", name="SQL Server", connect=connect)

    with pytest.raises(GatewayError) as error:
        gateway.query(GatewayQuery("SELECT amount FROM sales"))

    assert error.value.code == "E_GATEWAY_TIMEOUT"
    assert error.value.retryable is True
    assert "secret" not in str(error.value)
