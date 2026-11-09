"""HTTP endpoints for durable data tasks."""
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException
from app.auth import require_identity
from app.schemas.tasks import TaskCreateRequest, TaskStatusRequest
from app.schemas.feedback import TaskFeedbackRequest
from app.task_runtime import get_task_service
from app.services.feedback_service import FeedbackService
from app.services.conversation_service import build_task_conversation
from app.audit import write_audit
from app.config import settings
from app.guardrails import QueryError
from app.services.reflection_trace_service import ReflectionTraceService

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _serialize(value):
    data = asdict(value)
    for field in ("created_at", "updated_at"):
        if data.get(field):
            data[field] = getattr(value, field).isoformat()
    return data


@router.post("")
def create_task(request: TaskCreateRequest, identity: dict = Depends(require_identity)):
    task = get_task_service().create(question=request.question, tenant_id=identity.get("tenant_id", "default"), owner_id=str(identity.get("subject", "")), datasource_id=request.datasource, title=request.title)
    return _serialize(task)


@router.get("")
def list_tasks(limit: int = 20, identity: dict = Depends(require_identity)):
    tasks = get_task_service().list(tenant_id=identity.get("tenant_id", "default"), owner_id=str(identity.get("subject", "")), limit=limit)
    return [_serialize(task) for task in tasks]


@router.get("/{task_id}")
def get_task(task_id: str, identity: dict = Depends(require_identity)):
    try:
        detail = get_task_service().detail(task_id=task_id, tenant_id=identity.get("tenant_id", "default"), owner_id=str(identity.get("subject", "")))
    except LookupError:
        raise HTTPException(status_code=404, detail="任务不存在")
    steps = [_serialize(item) for item in detail["steps"]]
    events = [_serialize(item) for item in detail["events"]]
    if "viewer" in (identity.get("roles") or []):
        for step in steps:
            if step.get("kind") == "reflection":
                step["output"] = ReflectionTraceService.viewer_projection(step.get("output") or {})
            elif step.get("kind") == "verify_result":
                output = dict(step.get("output") or {})
                for field in ("reflection", "suggested_reflection"):
                    if isinstance(output.get(field), dict):
                        output[field] = ReflectionTraceService.viewer_projection(output[field])
                output.pop("evidence", None)
                step["output"] = output
        for event in events:
            if event.get("kind") == "reflection_recorded":
                event["payload"] = ReflectionTraceService.viewer_projection(event.get("payload") or {})
    return {
        **_serialize(detail["task"]),
        "steps": steps,
        "events": events,
        "artifacts": [_serialize(item) for item in detail["artifacts"]],
    }


@router.get("/{task_id}/conversation")
def get_task_conversation(task_id: str, identity: dict = Depends(require_identity)):
    try:
        detail = get_task_service().detail(
            task_id=task_id,
            tenant_id=identity.get("tenant_id", "default"),
            owner_id=str(identity.get("subject", "")),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    return build_task_conversation(detail)


@router.get("/{task_id}/branches")
def get_task_branches(task_id: str, identity: dict = Depends(require_identity)):
    service = get_task_service()
    tenant_id = identity.get("tenant_id", "default")
    owner_id = str(identity.get("subject", ""))
    try:
        family = service.branch_family(
            task_id=task_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc

    items = []
    for task in family:
        detail = service.detail(
            task_id=task.id,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        conversation = build_task_conversation(detail)
        latest = next(
            (turn for turn in reversed(conversation.get("turns", [])) if turn.get("status") == "completed"),
            {},
        )
        result = latest.get("result") or {}
        items.append({
            "task_id": task.id,
            "kind": "branch" if task.parent_task_id else "main",
            "parent_task_id": task.parent_task_id,
            "root_task_id": task.root_task_id or task.id,
            "source_trace_id": task.branch_source_trace_id,
            "label": task.branch_label or task.display_title(),
            "depth": int(task.branch_depth),
            "title": task.display_title(),
            "question": task.question,
            "status": task.status,
            "answer": str(result.get("answer", "")),
            "row_count": int(result.get("row_count", 0) or 0),
            "columns": list(result.get("columns", []) or [])[:100],
            "trace_id": str(latest.get("trace_id", "")),
        })
    root_task_id = family[0].root_task_id or family[0].id if family else task_id
    return {"root_task_id": root_task_id, "items": items}


@router.patch("/{task_id}/status")
def update_task_status(task_id: str, request: TaskStatusRequest, identity: dict = Depends(require_identity)):
    service = get_task_service()
    task = service.repository.get(task_id, identity.get("tenant_id", "default"))
    if task is None or task.owner_id != str(identity.get("subject", "")):
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return _serialize(service.transition(task_id, identity.get("tenant_id", "default"), request.status))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _validate_feedback_sql(sql: str, *, datasource_id: str, identity: dict) -> str:
    from app.datasources import datasource_schema, get_datasource, role_can_use
    from app.gateways.remote_sql import normalize_remote_sql
    from app.guardrails import validate_sql
    from app.policy import apply_policy, filter_schema

    datasource = get_datasource(datasource_id)
    if not datasource or not role_can_use(identity, datasource):
        raise LookupError("E_DATASOURCE_FORBIDDEN:数据源不存在或无权访问")
    visible = filter_schema(
        {table: sorted(columns) for table, columns in datasource_schema(datasource).items()},
        {**identity, "datasource": datasource_id},
    )
    normalized = normalize_remote_sql(sql, datasource.get("backend"))
    return apply_policy(
        validate_sql(normalized, {table: set(columns) for table, columns in visible.items()}, settings.max_sql_length, dialect=datasource.get("backend", "sqlite"), max_params=settings.max_params),
        {**identity, "datasource": datasource_id},
    )


@router.post("/{task_id}/feedback")
def record_task_feedback(task_id: str, request: TaskFeedbackRequest, identity: dict = Depends(require_identity)):
    service = get_task_service()
    tenant_id = identity.get("tenant_id", "default")
    owner_id = str(identity.get("subject", ""))
    try:
        detail = service.detail(task_id=task_id, tenant_id=tenant_id, owner_id=owner_id)
        result = FeedbackService(service).record(
            task_id=task_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            outcome=request.outcome,
            note=request.note,
            corrected_sql=request.corrected_sql,
            validate_correction=lambda sql: _validate_feedback_sql(sql, datasource_id=detail["task"].datasource_id, identity=identity),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except QueryError as exc:
        status = 403 if "FORBIDDEN" in exc.code else 422
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc
    except ValueError as exc:
        code, _, message = str(exc).partition(":")
        raise HTTPException(status_code=409, detail={"code": code or "E_FEEDBACK", "message": message or str(exc)}) from exc
    write_audit(
        settings.audit_log_path,
        trace_id=result["feedback_id"],
        task_id=task_id,
        tenant_id=tenant_id,
        sql="",
        duration_ms=0,
        event="feedback_recorded",
        actor_id=owner_id,
    )
    return result


@router.get("/{task_id}/feedback")
def list_task_feedback(task_id: str, identity: dict = Depends(require_identity)):
    try:
        detail = get_task_service().detail(
            task_id=task_id,
            tenant_id=identity.get("tenant_id", "default"),
            owner_id=str(identity.get("subject", "")),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    events = [_serialize(item) for item in detail["events"] if item.kind == "feedback_recorded"]
    cases = [_serialize(item) for item in detail["artifacts"] if item.kind == "evaluation_case"]
    return {"feedback": events, "offline_cases": cases}
