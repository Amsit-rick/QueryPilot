"""Authenticated, read-only Agent skill discovery and routing endpoints."""

from dataclasses import asdict
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import require_manage_access, require_query_access
from app.datasources import get_datasource, role_can_use
from app.agent import build_sql_generator
from app.agent.langchain_adapter import generate_sql as generate_sql_langchain
from app.config import settings
from app.llm import generate_sql
from app.schemas.skills import (
    SkillCanaryRequest,
    SkillEvaluationRequest,
    SkillPreviewRequest,
    SkillRouteRequest,
    SkillVersionCreateRequest,
)
from app.skill_runtime import get_skill_service
from app.domain.skill import SkillDefinition
from app.skill_evaluation import compare_skill_catalogs, overlay_candidate_catalog
from app.skill_query_evaluation import compare_skill_query_quality, load_skill_query_cases
from app.model_operations_runtime import get_model_operations_service
from app.services.agent_query_service import AgentQueryService


router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def _version_payload(version) -> dict:
    payload = asdict(version)
    payload["definition"] = version.definition.model_dump(mode="json")
    payload["created_at"] = version.created_at.isoformat()
    payload["updated_at"] = version.updated_at.isoformat()
    return payload


def _release_call(call):
    try:
        return _version_payload(call())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        status = 409 if "STATE" in str(exc) or "DUPLICATE" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _validate_datasource(identity: dict, datasource_id: str | None) -> None:
    if datasource_id is None:
        return
    datasource = get_datasource(datasource_id)
    if datasource is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="无权访问该数据源")


@router.get("")
def list_skills(identity: dict = Depends(require_query_access)):
    return {
        "version": 2,
        "read_only": True,
        "skills": get_skill_service().list_skills(identity),
    }


@router.get("/admin/versions")
def list_skill_versions(
    skill_id: str = Query(default="", max_length=64),
    identity: dict = Depends(require_manage_access),
):
    tenant_id = identity.get("tenant_id", "default")
    return [
        _version_payload(item)
        for item in get_skill_service().list_versions(tenant_id, skill_id)
    ]


@router.get("/admin/catalog")
def list_skill_admin_catalog(identity: dict = Depends(require_manage_access)):
    return get_skill_service().list_admin_catalog(identity)


@router.post("/admin/versions")
def create_skill_version(
    request: SkillVersionCreateRequest,
    identity: dict = Depends(require_manage_access),
):
    return _release_call(
        lambda: get_skill_service().create_version(
            tenant_id=identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
            definition=request.definition,
        )
    )


@router.post("/admin/versions/{version_id}/canary")
def canary_skill_version(
    version_id: str,
    request: SkillCanaryRequest,
    identity: dict = Depends(require_manage_access),
):
    return _release_call(
        lambda: get_skill_service().start_canary(
            version_id,
            tenant_id=identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
            percentage=request.percentage,
        )
    )


@router.post("/admin/versions/{version_id}/publish")
def publish_skill_version(
    version_id: str, identity: dict = Depends(require_manage_access)
):
    return _release_call(
        lambda: get_skill_service().publish_version(
            version_id,
            tenant_id=identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
        )
    )


@router.post("/admin/versions/{version_id}/rollback")
def rollback_skill_version(
    version_id: str, identity: dict = Depends(require_manage_access)
):
    return _release_call(
        lambda: get_skill_service().rollback_version(
            version_id,
            tenant_id=identity.get("tenant_id", "default"),
            actor_id=str(identity.get("subject", "")),
        )
    )


@router.post("/admin/preview")
def preview_skill_resolution(
    request: SkillPreviewRequest,
    identity: dict = Depends(require_manage_access),
):
    return get_skill_service().preview_resolution(
        tenant_id=identity.get("tenant_id", "default"),
        subject=request.subject,
        skill_id=request.skill_id,
    )


@router.post("/admin/evaluate")
def evaluate_skill_candidate(
    request: SkillEvaluationRequest,
    identity: dict = Depends(require_manage_access),
):
    service = get_skill_service()
    catalog = service.list_admin_catalog(identity)
    baseline = [
        {key: value for key, value in item.items() if key != "deployment"}
        for item in catalog
    ]
    if request.definition.id not in {item["id"] for item in baseline}:
        raise HTTPException(status_code=422, detail="候选版本必须基于当前 Skill 目录中的条目")
    base_definitions = tuple(SkillDefinition.model_validate(item) for item in baseline)
    candidate = overlay_candidate_catalog(
        base_definitions,
        [request.definition.model_dump(mode="json")],
    )
    cases_path = Path(__file__).resolve().parents[2] / "data" / "skill_routing_eval.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
    return compare_skill_catalogs(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
    )


@router.post("/admin/evaluate-query-quality")
async def evaluate_skill_query_candidate(
    request: SkillEvaluationRequest,
    identity: dict = Depends(require_manage_access),
):
    service = get_skill_service()
    catalog = service.list_admin_catalog(identity)
    baseline = [
        {key: value for key, value in item.items() if key != "deployment"}
        for item in catalog
    ]
    if request.definition.id not in {item["id"] for item in baseline}:
        raise HTTPException(status_code=422, detail="候选版本必须基于当前 Skill 目录中的条目")
    if not request.definition.model_fields_set.intersection({"trigger_terms", "intents", "workflow_steps", "description"}):
        raise HTTPException(status_code=422, detail="候选定义没有包含可评测的行为声明")

    runtime_model = get_model_operations_service().resolve_runtime(identity.get("tenant_id", "default"))
    if not runtime_model.api_key:
        raise HTTPException(status_code=409, detail="未配置模型 API Key，无法执行 SQL 效果评测")

    base_definitions = tuple(SkillDefinition.model_validate(item) for item in baseline)
    candidate = overlay_candidate_catalog(
        base_definitions,
        [request.definition.model_dump(mode="json")],
    )
    cases_path = Path(__file__).resolve().parents[2] / "data" / "skill_query_eval.json"
    dataset_version, cases = load_skill_query_cases(cases_path)
    query_generator = AgentQueryService(
        provider=runtime_model.adapter,
        direct_generate=generate_sql,
        langchain_generate=generate_sql_langchain,
        build_langchain=build_sql_generator,
        api_key=runtime_model.api_key,
        base_url=runtime_model.base_url,
        model=runtime_model.model,
        timeout=settings.model_timeout_seconds,
        system_prompt=runtime_model.system_prompt,
        prompt_version=runtime_model.prompt_version,
    )

    async def generate_candidate(*, question, schema, context, dialect):
        return await query_generator.generate(
            question=question,
            schema=schema,
            context=context,
            dialect=dialect,
            direct_kwargs={
                "model": runtime_model.model,
                "timeout": settings.model_timeout_seconds,
                "api_key": runtime_model.api_key,
                "base_url": runtime_model.base_url,
                "cache_ttl": settings.sql_cache_ttl_seconds,
                "cache_max_entries": settings.sql_cache_max_entries,
                "system_prompt": runtime_model.system_prompt,
                "prompt_version": runtime_model.prompt_version,
            },
        )

    report = await compare_skill_query_quality(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
        generate=generate_candidate,
        dataset_version=dataset_version,
    )
    report["model"] = runtime_model.model
    report["prompt_version"] = runtime_model.prompt_version
    return report


@router.post("/route")
def route_skill(request: SkillRouteRequest, identity: dict = Depends(require_query_access)):
    _validate_datasource(identity, request.datasource_id)
    return get_skill_service().route(
        request.question,
        identity=identity,
        datasource_id=request.datasource_id,
    )


__all__ = ["router"]
