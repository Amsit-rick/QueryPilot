from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain.query_admission import QueryRequestAdmission
from app.main import app
from app.repositories.memory_query_admission_repository import InMemoryQueryAdmissionRepository
from app.schemas.queries import QueryRequest
from app.services.query_admission_service import IdempotencyConflict, QueryAdmissionService
from app.services.query_application_service import QueryApplicationDependencies, QueryApplicationService
from app.task_runtime import get_task_service


def _request(**overrides) -> QueryRequest:
    payload = {
        "question": "查询华东销售额",
        "sql": "SELECT region, amount FROM sales WHERE region = :region",
        "params": {"region": "华东"},
        "datasource": "sqlite-demo",
        "max_rows": 100,
        "client_request_id": f"request-{uuid4()}",
    }
    payload.update(overrides)
    return QueryRequest(**payload)


def _service() -> QueryAdmissionService:
    return QueryAdmissionService(InMemoryQueryAdmissionRepository())


def test_first_admission_claims_execution_and_duplicate_reuses_task_id():
    service = _service()
    request = _request(client_request_id="same-request")

    first = service.admit(request=request, tenant_id="tenant-a", owner_id="user-a")
    duplicate = service.admit(request=request, tenant_id="tenant-a", owner_id="user-a")

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.admission.task_id == first.admission.task_id
    assert duplicate.admission.request_sha256 == first.admission.request_sha256


def test_same_id_with_different_execution_payload_is_rejected():
    service = _service()
    service.admit(
        request=_request(client_request_id="conflict-request", question="查询华东销售额"),
        tenant_id="tenant-a",
        owner_id="user-a",
    )

    with pytest.raises(IdempotencyConflict, match="E_IDEMPOTENCY_CONFLICT"):
        service.admit(
            request=_request(client_request_id="conflict-request", question="查询华南销售额"),
            tenant_id="tenant-a",
            owner_id="user-a",
        )


def test_idempotency_scope_is_isolated_by_tenant_and_owner():
    service = _service()
    request = _request(client_request_id="shared-client-id")

    tenant_a = service.admit(request=request, tenant_id="tenant-a", owner_id="user-a")
    tenant_b = service.admit(request=request, tenant_id="tenant-b", owner_id="user-a")
    user_b = service.admit(request=request, tenant_id="tenant-a", owner_id="user-b")

    assert tenant_a.created is tenant_b.created is user_b.created is True
    assert len({tenant_a.admission.task_id, tenant_b.admission.task_id, user_b.admission.task_id}) == 3


def test_concurrent_duplicate_admission_has_exactly_one_execution_owner():
    service = _service()
    request = _request(client_request_id="concurrent-request")

    def admit_once(_index: int):
        return service.admit(request=request, tenant_id="tenant-a", owner_id="user-a")

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(admit_once, range(10)))

    assert sum(result.created for result in results) == 1
    assert len({result.admission.task_id for result in results}) == 1


def test_application_concurrency_invokes_query_workflow_only_once():
    calls = 0
    service = QueryApplicationService(
        QueryApplicationDependencies(
            generate_sql=lambda *_args, **_kwargs: "SELECT 1",
            create_query_graph=lambda *_args, **_kwargs: None,
            describe_result=lambda *_args, **_kwargs: {},
            get_skill_service=lambda: None,
            get_user_grants=lambda *_args, **_kwargs: [],
        )
    )

    async def fake_execute_once(_request, _identity, *, reserved_task_id=None):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"task_id": reserved_task_id, "rows": [], "row_count": 0}

    service._execute_once = fake_execute_once
    request = _request(client_request_id=f"application-concurrent-{uuid4()}")
    identity = {"tenant_id": "tenant-concurrency", "subject": "user-a"}

    async def run_concurrently():
        return await asyncio.gather(
            service.execute(request, identity),
            service.execute(request, identity),
        )

    first, duplicate = asyncio.run(run_concurrently())

    assert calls == 1
    assert first["task_id"] == duplicate["task_id"]
    assert duplicate == {
        "task_id": first["task_id"],
        "status": "running",
        "idempotent_replay": True,
    }


def test_admission_record_contains_only_request_fingerprint_not_sensitive_payload():
    service = _service()
    result = service.admit(
        request=_request(client_request_id="redacted-request", params={"region": "机密区域"}),
        tenant_id="tenant-a",
        owner_id="user-a",
    )

    stored = asdict(result.admission)
    serialized = repr(stored)
    assert "机密区域" not in serialized
    assert "SELECT region" not in serialized
    assert "rows" not in stored
    assert set(stored) == {
        "tenant_id",
        "owner_id",
        "operation",
        "client_request_id",
        "request_sha256",
        "task_id",
        "status",
        "error_code",
        "created_at",
        "updated_at",
    }


def test_admission_completion_and_failure_are_persisted():
    service = _service()
    completed = service.admit(
        request=_request(client_request_id="completed-request"),
        tenant_id="tenant-a",
        owner_id="user-a",
    ).admission
    failed = service.admit(
        request=_request(client_request_id="failed-request"),
        tenant_id="tenant-a",
        owner_id="user-a",
    ).admission

    service.complete(completed)
    service.fail(failed, error_code="E_QUERY_ERROR")

    assert service.get(completed.key).status == "completed"
    persisted_failure = service.get(failed.key)
    assert persisted_failure.status == "failed"
    assert persisted_failure.error_code == "E_QUERY_ERROR"


@pytest.mark.parametrize(
    "value",
    ["contains spaces", "中文请求", "slash/not-allowed", "a" * 129],
)
def test_client_request_id_rejects_unsafe_values(value: str):
    with pytest.raises(ValidationError):
        _request(client_request_id=value)


def test_query_api_duplicate_returns_same_task_without_duplicate_side_effects():
    client = TestClient(app)
    client_request_id = f"api-{uuid4()}"
    payload = {
        "client_request_id": client_request_id,
        "question": "查询区域",
        "sql": "SELECT region FROM sales ORDER BY id",
    }

    first = client.post("/api/v1/query", json=payload)
    assert first.status_code == 200
    first_body = first.json()
    detail_before = get_task_service().detail_for_tenant(
        task_id=first_body["task_id"], tenant_id="default"
    )
    counts_before = tuple(
        len(detail_before[name]) for name in ("steps", "events", "artifacts")
    )

    duplicate = client.post("/api/v1/query", json=payload)
    assert duplicate.status_code == 200
    duplicate_body = duplicate.json()
    detail_after = get_task_service().detail_for_tenant(
        task_id=first_body["task_id"], tenant_id="default"
    )

    assert duplicate_body["task_id"] == first_body["task_id"]
    assert duplicate_body["row_count"] == first_body["row_count"]
    assert duplicate_body["rows"] == first_body["rows"][: len(duplicate_body["rows"])]
    assert duplicate_body["idempotent_replay"] is True
    assert tuple(len(detail_after[name]) for name in ("steps", "events", "artifacts")) == counts_before


def test_query_api_rejects_reusing_id_for_different_request():
    client = TestClient(app)
    client_request_id = f"api-conflict-{uuid4()}"
    first = client.post(
        "/api/v1/query",
        json={
            "client_request_id": client_request_id,
            "question": "查询区域",
            "sql": "SELECT region FROM sales",
        },
    )
    assert first.status_code == 200

    conflict = client.post(
        "/api/v1/query",
        json={
            "client_request_id": client_request_id,
            "question": "查询销售额",
            "sql": "SELECT amount FROM sales",
        },
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "E_IDEMPOTENCY_CONFLICT"


def test_follow_up_duplicate_does_not_open_another_turn():
    client = TestClient(app)
    initial = client.post(
        "/api/v1/query",
        json={"question": "查询区域", "sql": "SELECT region FROM sales ORDER BY id"},
    ).json()
    follow_up_payload = {
        "client_request_id": f"follow-up-{uuid4()}",
        "task_id": initial["task_id"],
        "follow_up": True,
        "question": "只看华东",
        "sql": "SELECT region FROM sales WHERE region = :region ORDER BY id",
        "params": {"region": "华东"},
    }

    first = client.post("/api/v1/query", json=follow_up_payload)
    assert first.status_code == 200
    detail_before = get_task_service().detail_for_tenant(
        task_id=initial["task_id"], tenant_id="default"
    )
    turn_count_before = sum(
        step.kind == "understand_question" for step in detail_before["steps"]
    )

    duplicate = client.post("/api/v1/query", json=follow_up_payload)
    assert duplicate.status_code == 200
    detail_after = get_task_service().detail_for_tenant(
        task_id=initial["task_id"], tenant_id="default"
    )

    assert duplicate.json()["task_id"] == initial["task_id"]
    assert duplicate.json()["idempotent_replay"] is True
    assert sum(step.kind == "understand_question" for step in detail_after["steps"]) == turn_count_before


def test_query_request_admission_domain_is_constructible_for_repository_contract():
    admission = QueryRequestAdmission(
        tenant_id="tenant-a",
        owner_id="user-a",
        operation="query",
        client_request_id="request-a",
        request_sha256="a" * 64,
        task_id=str(uuid4()),
    )

    assert admission.status == "running"
    assert admission.key == ("tenant-a", "user-a", "query", "request-a")
