import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_manage_access
from app.model_operations_runtime import get_model_operations_service
from app.schemas.model_operations import PromptCreateRequest, ProviderCreateRequest, ProviderUpdateRequest

router = APIRouter(prefix="/api/v1/model-ops", tags=["model-operations"])


def _provider_payload(provider):
    data = asdict(provider)
    for field in ("created_at", "updated_at", "last_checked_at"):
        data[field] = data[field].isoformat() if data.get(field) else None
    data["credential_configured"] = bool(os.getenv(provider.api_key_env, ""))
    return data


def _prompt_payload(prompt):
    data = asdict(prompt)
    data["created_at"] = prompt.created_at.isoformat()
    return data


@router.get("/providers")
def list_providers(identity=Depends(require_manage_access)):
    return [_provider_payload(item) for item in get_model_operations_service().list_providers(identity.get("tenant_id", "default"))]


@router.post("/providers")
def create_provider(request: ProviderCreateRequest, identity=Depends(require_manage_access)):
    try:
        provider = get_model_operations_service().create_provider(tenant_id=identity.get("tenant_id", "default"), **request.model_dump())
        return _provider_payload(provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: str, request: ProviderUpdateRequest, identity=Depends(require_manage_access)):
    try:
        provider = get_model_operations_service().update_provider(provider_id, identity.get("tenant_id", "default"), request.model_dump(exclude_none=True))
        return _provider_payload(provider)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/providers/{provider_id}/health")
async def check_provider_health(provider_id: str, identity=Depends(require_manage_access)):
    try:
        provider = await get_model_operations_service().check_health(provider_id, identity.get("tenant_id", "default"))
        return _provider_payload(provider)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/prompts")
def list_prompts(name: str = "", identity=Depends(require_manage_access)):
    return [_prompt_payload(item) for item in get_model_operations_service().list_prompts(identity.get("tenant_id", "default"), name)]


@router.post("/prompts")
def create_prompt(request: PromptCreateRequest, identity=Depends(require_manage_access)):
    try:
        prompt = get_model_operations_service().create_prompt(tenant_id=identity.get("tenant_id", "default"), owner_id=str(identity.get("subject", "")), **request.model_dump())
        return _prompt_payload(prompt)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/prompts/{prompt_id}/activate")
def activate_prompt(prompt_id: str, identity=Depends(require_manage_access)):
    try:
        return _prompt_payload(get_model_operations_service().activate_prompt(prompt_id, identity.get("tenant_id", "default")))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runtime")
def runtime_configuration(identity=Depends(require_manage_access)):
    runtime = get_model_operations_service().resolve_runtime(identity.get("tenant_id", "default"))
    return {"provider_id": runtime.provider_id, "provider_name": runtime.provider_name, "adapter": runtime.adapter, "model": runtime.model, "prompt_id": runtime.prompt_id, "prompt_version": runtime.prompt_version, "source": runtime.source, "credential_configured": bool(runtime.api_key)}
