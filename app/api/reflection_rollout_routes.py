"""Administrative API for tenant-scoped reflection rollout versions."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_manage_access
from app.config import settings
from app.reflection_rollout_runtime import get_reflection_rollout_service
from app.schemas.reflection_rollouts import (
    ReflectionRolloutCanaryRequest,
    ReflectionRolloutCreateRequest,
    ReflectionRolloutHealthRequest,
    ReflectionRolloutPreviewRequest,
)


router = APIRouter(prefix="/api/v1/reflection-rollouts", tags=["reflection-rollouts"])


def _payload(version) -> dict:
    value = asdict(version)
    value["policy"] = version.policy.model_dump(mode="json")
    value["created_at"] = version.created_at.isoformat()
    value["updated_at"] = version.updated_at.isoformat()
    return value


def _call(call):
    try:
        return _payload(call())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "STATE" in str(exc) or "DUPLICATE" in str(exc) or "TARGET" in str(exc) else 422, detail=str(exc)) from exc


@router.get("/admin/versions")
def list_versions(identity: dict = Depends(require_manage_access)):
    return [_payload(item) for item in get_reflection_rollout_service().repository.list(identity.get("tenant_id", "default"))]


@router.post("/admin/versions")
def create_version(request: ReflectionRolloutCreateRequest, identity: dict = Depends(require_manage_access)):
    return _call(lambda: get_reflection_rollout_service().create_version(
        tenant_id=identity.get("tenant_id", "default"),
        actor_id=str(identity.get("subject", "")),
        policy=request.policy,
    ))


@router.post("/admin/versions/{version_id}/canary")
def start_canary(version_id: str, request: ReflectionRolloutCanaryRequest, identity: dict = Depends(require_manage_access)):
    return _call(lambda: get_reflection_rollout_service().start_canary(
        version_id,
        tenant_id=identity.get("tenant_id", "default"),
        actor_id=str(identity.get("subject", "")),
        percentage=request.percentage,
    ))


@router.post("/admin/versions/{version_id}/publish")
def publish(version_id: str, identity: dict = Depends(require_manage_access)):
    return _call(lambda: get_reflection_rollout_service().publish(
        version_id,
        tenant_id=identity.get("tenant_id", "default"),
        actor_id=str(identity.get("subject", "")),
    ))


@router.post("/admin/versions/{version_id}/rollback")
def rollback(version_id: str, identity: dict = Depends(require_manage_access)):
    return _call(lambda: get_reflection_rollout_service().rollback(
        version_id,
        tenant_id=identity.get("tenant_id", "default"),
        actor_id=str(identity.get("subject", "")),
    ))


@router.post("/admin/versions/{version_id}/evaluate-health")
def evaluate_health(
    version_id: str,
    request: ReflectionRolloutHealthRequest,
    identity: dict = Depends(require_manage_access),
):
    try:
        return get_reflection_rollout_service().evaluate_health(
            version_id,
            tenant_id=identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
            metrics=request.model_dump(),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/admin/preview")
def preview(request: ReflectionRolloutPreviewRequest, identity: dict = Depends(require_manage_access)):
    decision = get_reflection_rollout_service().resolve(
        tenant_id=identity.get("tenant_id", "default"),
        subject=request.subject,
        default_mode=settings.agent_reflection_mode,
        gate_report_path=settings.agent_reflection_release_gate_path,
    )
    return asdict(decision)


__all__ = ["router"]

