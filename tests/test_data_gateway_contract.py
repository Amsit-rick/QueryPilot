from dataclasses import replace

import pytest

from app.domain.data_gateway import (
    GatewayCapabilities,
    GatewayDescriptor,
    GatewayError,
    GatewayHealth,
    GatewayQuery,
    GatewayResult,
)
from app.gateways.registry import GatewayRegistry
from app.services.data_gateway_service import DataGatewayService


class FakeGateway:
    descriptor = GatewayDescriptor(id="demo", name="Demo", backend="fake", dialect="sqlite")
    capabilities = GatewayCapabilities(preview=True, cancel=True)

    def __init__(self):
        self.calls = []

    def test_connection(self):
        self.calls.append(("test_connection",))
        return GatewayHealth(status="healthy", message="连接成功", latency_ms=3)

    def schema(self):
        self.calls.append(("schema",))
        return {"sales": ["id", "amount"]}

    def preview(self, table, limit=20, offset=0):
        self.calls.append(("preview", table, limit, offset))
        return GatewayResult(columns=["id"], rows=[[1]], row_count=1, truncated=False)

    def query(self, request):
        self.calls.append(("query", request))
        return GatewayResult(columns=["amount"], rows=[[10]], row_count=1, truncated=False, operation_id="op-1")

    def cancel(self, operation_id):
        self.calls.append(("cancel", operation_id))
        return True

    def health(self):
        return GatewayHealth(status="healthy", message="可用")


def test_gateway_registry_requires_unique_ids_and_resolves_registered_gateway():
    registry = GatewayRegistry()
    gateway = FakeGateway()
    registry.register(gateway)

    assert registry.get("demo") is gateway
    assert registry.list_descriptors() == [gateway.descriptor]
    with pytest.raises(ValueError, match="E_GATEWAY_DUPLICATE"):
        registry.register(FakeGateway())
    with pytest.raises(GatewayError) as exc:
        registry.require("missing")
    assert exc.value.code == "E_GATEWAY_NOT_FOUND"


def test_gateway_service_enforces_access_and_request_bounds():
    gateway = FakeGateway()
    service = DataGatewayService(GatewayRegistry([gateway]), can_use=lambda identity, descriptor: "analyst" in identity.get("roles", []), max_rows=100)

    assert service.schema({"roles": ["analyst"]}, "demo") == {"sales": ["id", "amount"]}
    result = service.query({"roles": ["analyst"]}, "demo", GatewayQuery(sql="SELECT amount FROM sales", params={"region": "east"}, max_rows=10))
    assert result.operation_id == "op-1"
    assert gateway.calls[-1][1].max_rows == 10
    with pytest.raises(GatewayError) as exc:
        service.schema({"roles": ["viewer"]}, "demo")
    assert exc.value.code == "E_GATEWAY_FORBIDDEN"
    with pytest.raises(GatewayError) as exc:
        service.query({"roles": ["analyst"]}, "demo", GatewayQuery(sql="SELECT 1", max_rows=101))
    assert exc.value.code == "E_GATEWAY_LIMIT"


def test_gateway_service_checks_declared_capabilities():
    gateway = FakeGateway()
    gateway.capabilities = replace(gateway.capabilities, preview=False, cancel=False)
    service = DataGatewayService(GatewayRegistry([gateway]), can_use=lambda _identity, _descriptor: True)

    with pytest.raises(GatewayError) as preview_error:
        service.preview({}, "demo", "sales")
    assert preview_error.value.code == "E_GATEWAY_UNSUPPORTED"
    with pytest.raises(GatewayError) as cancel_error:
        service.cancel({}, "demo", "op-1")
    assert cancel_error.value.code == "E_GATEWAY_UNSUPPORTED"


def test_gateway_service_normalizes_preview_and_cancel_contracts():
    gateway = FakeGateway()
    service = DataGatewayService(GatewayRegistry([gateway]), can_use=lambda _identity, _descriptor: True)

    assert service.preview({}, "demo", "sales").rows == [[1]]
    assert service.cancel({}, "demo", "op-1") is True

    class InvalidOperationsGateway(FakeGateway):
        def preview(self, table, limit=20, offset=0):
            return {"columns": ["id"], "rows": [[1]]}

        def cancel(self, operation_id):
            return "yes"

    invalid = DataGatewayService(
        GatewayRegistry([InvalidOperationsGateway()]), can_use=lambda _identity, _descriptor: True
    )
    with pytest.raises(GatewayError, match="preview 返回格式无效") as preview_error:
        invalid.preview({}, "demo", "sales")
    assert preview_error.value.code == "E_GATEWAY_CONTRACT"
    with pytest.raises(GatewayError, match="cancel 返回格式无效") as cancel_error:
        invalid.cancel({}, "demo", "op-1")
    assert cancel_error.value.code == "E_GATEWAY_CONTRACT"


def test_gateway_service_validates_preview_and_cancel_arguments():
    service = DataGatewayService(
        GatewayRegistry([FakeGateway()]), can_use=lambda _identity, _descriptor: True, max_preview_rows=10
    )
    for limit, offset in ((0, 0), (-1, 0), (11, 0), (1, -1)):
        with pytest.raises(GatewayError) as error:
            service.preview({}, "demo", "sales", limit=limit, offset=offset)
        assert error.value.code == "E_GATEWAY_LIMIT"
    with pytest.raises(GatewayError) as error:
        service.cancel({}, "demo", "  ")
    assert error.value.code == "E_GATEWAY_LIMIT"


def test_gateway_service_preview_and_cancel_failures_are_safe():
    class BrokenOperationsGateway(FakeGateway):
        def preview(self, table, limit=20, offset=0):
            raise RuntimeError("password=super-secret host=db.internal")

        def cancel(self, operation_id):
            raise RuntimeError("password=super-secret host=db.internal")

    service = DataGatewayService(
        GatewayRegistry([BrokenOperationsGateway()]), can_use=lambda _identity, _descriptor: True
    )
    with pytest.raises(GatewayError) as preview_error:
        service.preview({}, "demo", "sales")
    assert preview_error.value.code == "E_GATEWAY_PREVIEW"
    assert preview_error.value.retryable is True
    assert "super-secret" not in str(preview_error.value)
    with pytest.raises(GatewayError) as cancel_error:
        service.cancel({}, "demo", "op-1")
    assert cancel_error.value.code == "E_GATEWAY_CANCEL"
    assert cancel_error.value.retryable is True
    assert "db.internal" not in str(cancel_error.value)


def test_gateway_service_normalizes_health_and_connection_contracts():
    gateway = FakeGateway()
    service = DataGatewayService(GatewayRegistry([gateway]), can_use=lambda _identity, _descriptor: True)

    assert service.test_connection({}, "demo").status == "healthy"
    assert service.health({}, "demo").status == "healthy"

    class InvalidHealthGateway(FakeGateway):
        def test_connection(self):
            return {"status": "healthy"}

        def health(self):
            return {"status": "healthy"}

    invalid = DataGatewayService(
        GatewayRegistry([InvalidHealthGateway()]), can_use=lambda _identity, _descriptor: True
    )
    with pytest.raises(GatewayError, match="health 返回格式无效") as connection_error:
        invalid.test_connection({}, "demo")
    assert connection_error.value.code == "E_GATEWAY_CONTRACT"
    with pytest.raises(GatewayError, match="health 返回格式无效") as health_error:
        invalid.health({}, "demo")
    assert health_error.value.code == "E_GATEWAY_CONTRACT"


def test_gateway_service_health_failures_are_safe_and_retryable():
    class BrokenHealthGateway(FakeGateway):
        def health(self):
            raise RuntimeError("password=super-secret host=db.internal")

    service = DataGatewayService(
        GatewayRegistry([BrokenHealthGateway()]), can_use=lambda _identity, _descriptor: True
    )
    with pytest.raises(GatewayError) as error:
        service.health({}, "demo")
    assert error.value.code == "E_GATEWAY_HEALTH"
    assert error.value.retryable is True
    assert "super-secret" not in str(error.value)
    assert "db.internal" not in str(error.value)


def test_gateway_errors_expose_safe_diagnostics_without_secrets():
    class BrokenGateway(FakeGateway):
        def schema(self):
            raise RuntimeError("password=super-secret host=db.internal")

    service = DataGatewayService(GatewayRegistry([BrokenGateway()]), can_use=lambda _identity, _descriptor: True)
    with pytest.raises(GatewayError) as exc:
        service.schema({}, "demo")

    assert exc.value.code == "E_GATEWAY_SCHEMA"
    assert exc.value.retryable is True
    assert "super-secret" not in str(exc.value)
    assert "db.internal" not in str(exc.value)
    assert exc.value.operation == "schema"


def test_gateway_contract_validates_metadata_and_result_shapes():
    with pytest.raises(ValueError):
        GatewayDescriptor(id="", name="Demo", backend="fake", dialect="sqlite")
    with pytest.raises(ValueError, match="credentials"):
        GatewayDescriptor(id="demo", name="Demo", backend="fake", dialect="sqlite", metadata={"password": "secret"})
    with pytest.raises(ValueError):
        GatewayQuery(sql="", max_rows=10)
    with pytest.raises(ValueError):
        GatewayResult(columns=["id"], rows=[[1, 2]], row_count=1, truncated=False)
    with pytest.raises(ValueError):
        GatewayHealth(status="unknown", message="invalid")


def test_gateway_service_rejects_adapter_contract_violations():
    class InvalidGateway(FakeGateway):
        def schema(self):
            return {"sales": "id"}

        def query(self, request):
            return {"columns": ["id"], "rows": [[1]]}

    service = DataGatewayService(GatewayRegistry([InvalidGateway()]), can_use=lambda _identity, _descriptor: True)
    with pytest.raises(GatewayError, match="schema 返回格式无效") as schema_error:
        service.schema({}, "demo")
    assert schema_error.value.code == "E_GATEWAY_CONTRACT"
    with pytest.raises(GatewayError, match="query 返回格式无效") as query_error:
        service.query({}, "demo", GatewayQuery(sql="SELECT id FROM sales"))
    assert query_error.value.code == "E_GATEWAY_CONTRACT"
