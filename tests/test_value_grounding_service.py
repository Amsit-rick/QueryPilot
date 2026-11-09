from __future__ import annotations

from app.services.value_grounding_service import ValueGroundingService, parse_value_columns
from app.services import query_application_service as qas


def test_parses_and_deduplicates_configured_columns():
    pairs = parse_value_columns([
        "sales.status",
        "Sales.Status",
        "sales ..nope",
        "bad name.col",
        "sales.",
        "",
        None,
    ])

    assert pairs == [("sales", "status")]


def test_grounding_is_limited_to_authorized_tables_and_bounded_columns():
    calls: list[tuple[str, str]] = []

    def sampler(table, column, limit):
        calls.append((table, column))
        return ["paid", "pending"]

    service = ValueGroundingService(sampler, max_columns=1)
    context = service.context(
        datasource_id="ds",
        allowed_tables={"sales"},
        configured=["sales.status", "customers.level"],
    )

    assert context == {"sales": {"status": ["paid", "pending"]}}
    assert calls == [("sales", "status")]


def test_values_are_deduplicated_truncated_and_capped():
    def sampler(table, column, limit):
        return ["paid", "paid", "", None, "x" * 200, "pending", "refunded"]

    service = ValueGroundingService(sampler, max_values=3, max_value_chars=16)
    context = service.context(datasource_id="ds", allowed_tables={"payments"}, configured=["payments.status"])

    values = context["payments"]["status"]
    assert values == ["paid", "x" * 16, "pending"]


def test_sampler_failure_degrades_to_no_grounding():
    def sampler(table, column, limit):
        raise RuntimeError("database unavailable")

    service = ValueGroundingService(sampler)
    assert service.context(datasource_id="ds", allowed_tables={"sales"}, configured=["sales.status"]) == {}


def test_sampling_is_cached_until_the_ttl_expires():
    clock = [100.0]
    calls = []

    def sampler(table, column, limit):
        calls.append(column)
        return ["paid"]

    service = ValueGroundingService(sampler, ttl_seconds=60, clock=lambda: clock[0])
    for _ in range(3):
        service.context(datasource_id="ds", allowed_tables={"payments"}, configured=["payments.status"])

    assert calls == ["status"]
    clock[0] = 161.0
    service.context(datasource_id="ds", allowed_tables={"payments"}, configured=["payments.status"])
    assert calls == ["status", "status"]


def test_identifier_injection_is_rejected_before_sampling():
    calls = []
    service = ValueGroundingService(lambda t, c, n: calls.append(t) or ["x"])

    assert service.context(datasource_id="ds", allowed_tables={"sales"}, configured=["sales.id`; DROP TABLE sales"]) == {}
    assert calls == []

def test_pipeline_grounding_is_gated_by_the_feature_flag(monkeypatch):
    class FakeService:
        def context(self, **_kwargs):
            return {"sales": {"status": ["paid"]}}

    monkeypatch.setattr(qas.settings, "value_grounding_enabled", False)
    monkeypatch.setattr(qas, "get_value_grounding_service", lambda backend: FakeService())
    assert qas.ground_column_values({"id": "ds", "backend": "mysql"}, {"sales": {"status"}}) == {}

    monkeypatch.setattr(qas.settings, "value_grounding_enabled", True)
    assert qas.ground_column_values({"id": "ds", "backend": "mysql"}, {"sales": {"status"}}) == {
        "sales": {"status": ["paid"]}
    }


def test_pipeline_grounding_is_empty_for_unimplemented_backends(monkeypatch):
    monkeypatch.setattr(qas.settings, "value_grounding_enabled", True)
    monkeypatch.setattr(qas, "get_value_grounding_service", lambda backend: None)
    assert qas.ground_column_values({"id": "ds", "backend": "sqlite"}, {"sales": {"status"}}) == {}


def test_pipeline_grounding_degrades_on_service_failure(monkeypatch):
    class BoomService:
        def context(self, **_kwargs):
            raise RuntimeError("sampler exploded")

    monkeypatch.setattr(qas.settings, "value_grounding_enabled", True)
    monkeypatch.setattr(qas, "get_value_grounding_service", lambda backend: BoomService())
    assert qas.ground_column_values({"id": "ds", "backend": "mysql"}, {"sales": {"status"}}) == {}
