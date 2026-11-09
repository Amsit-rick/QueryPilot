from uuid import uuid4
from time import perf_counter, time
import secrets
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Request, Response, Cookie, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from .config import settings
from .db import execute_readonly, schema
from .guardrails import QueryError, validate_sql
from .gateways.remote_sql import normalize_remote_sql
from .llm import ModelConfigurationError, generate_sql
from .audit import write_audit, summarize
from .retrieval import load_catalog, retrieve_context
from .exporter import export_csv, write_csv
from .export_store import create_job as db_create_export, update_job as db_update_export, get_job as db_get_export, delete_expired as db_delete_expired
from .export_queue import enqueue_export, list_dead_letters, retry_dead_letter
from .auth import require_identity
from .rbac import authorize
from .policy import apply_policy, redact_rows, apply_row_filter, filter_schema, is_safe_scope_column, get_role_policy, update_role_policy
from .database import database_status
from .rate_limit import enforce_rate_limit
from .mysql_db import mysql_schema, execute_mysql
from .budget import cost_for_tokens, enforce_budget, estimate_query_cost, estimate_query_usage, settle_budget
from .datasources import list_data_sources_for, list_datasources, get_datasource, role_can_use, datasource_schema, datasource_schema_details, execute_datasource, test_connection
from .agent import AnalysisPlan, ResultArtifact, ResultExplanation, VerificationResult, create_query_graph, save_checkpoint, build_sql_generator
from .agent.langchain_adapter import generate_sql as generate_sql_langchain
from .agent.state import QueryAgentState
from .agent.contracts import AnalysisMode, merge_sql_params, parse_sql_plan
from .schemas.queries import QueryRequest
from .api.dependencies import require_query_access, require_export_access
from .services.datasource_service import DataSourceAccessService
from .services.query_service import QueryExecutionService
from .services.agent_query_service import AgentQueryService
from .services.prompt_injection_service import PromptInjectionError, ensure_safe_generation_prompt
from .services.context_service import QueryContextService
from .services.task_context_service import build_follow_up_context
from .services.analysis_service import describe_result, infer_analysis_mode
from .services.quality_gate_service import ApprovalGateRequired, anomaly_gate_reasons, evaluate_pre_execution, relationship_gate_reasons
from .services.query_cost_service import evaluate_query_cost
from .services.query_application_service import QueryApplicationDependencies, QueryApplicationService
from .services.query_stream_service import stream_query_events
from .query_stream_runtime import get_durable_query_stream_service
from .services.durable_query_stream_service import QueryStreamCursorError
from .metrics_runtime import metrics_runtime_status, render_metrics, shutdown_metrics_runtime
from .schema_embedding_runtime import schema_embedding_runtime_status
from .services.relationship_service import resolve_relationships
from .services.schema_retrieval_service import ambiguous_path_gate_reasons, retrieve_schema
from .services.result_preview_service import build_result_preview
from .services.chart_artifact_service import build_chart_artifact_definition
from .semantic_runtime import get_semantic_service
from .task_runtime import get_task_service
from .tool_replay_runtime import get_tool_replay_policy_service
from .api.task_routes import router as task_router
from .api.semantic_routes import router as semantic_router
from .api.data_gateway_routes import router as data_gateway_router
from .api.model_operations_routes import router as model_operations_router
from .api.integration_routes import router as integration_router
from .api.skill_routes import router as skill_router
from .skill_runtime import get_skill_service
from .api.mcp_registry_routes import router as mcp_registry_router
from .api.reflection_rollout_routes import router as reflection_rollout_router
from .api.quality_admin_routes import router as quality_admin_router
from .api.proactive_analysis_routes import router as proactive_analysis_router
from .model_operations_runtime import get_model_operations_service
from .domain.data_gateway import GatewayError, GatewayQuery
from .gateways.factory import build_gateway_registry
from .services.data_gateway_service import DataGatewayService
from . import approval
from .approval_store import create_approval, list_approvals as db_list_approvals, get_approval as db_get_approval, set_status as db_set_approval_status
from .identity import hash_password, verify_password, issue_tokens, refresh_token_hash, decode_refresh_claims
from .identity_store import get_user, get_user_by_id, record_session, revoke_session, get_session, update_login_failure, clear_login_failures, save_query, list_queries, delete_query, revoke_user_sessions, update_password, update_profile, list_users, create_user, set_user_status, set_user_role, delete_user, get_user_profile, list_sessions, revoke_session_by_id, get_user_grants, add_user_grant, delete_user_grant
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from contextlib import asynccontextmanager
from .startup_checks import validate_production_config


def _gateway_http_exception(exc: GatewayError) -> HTTPException:
    """Map gateway failures to a stable public HTTP error contract."""
    if "FORBIDDEN" in exc.code:
        status = 403
    elif exc.code.endswith("NOT_FOUND") or exc.code.endswith("TABLE"):
        status = 404
    elif "LIMIT" in exc.code:
        status = 422
    elif "UNSUPPORTED" in exc.code:
        status = 409
    else:
        status = 502
    return HTTPException(status_code=status, detail=exc.as_dict())


def get_query_application_service() -> QueryApplicationService:
    """Build the query workflow with route-level patch points preserved."""
    return QueryApplicationService(
        QueryApplicationDependencies(
            generate_sql=generate_sql,
            create_query_graph=create_query_graph,
            describe_result=describe_result,
            get_skill_service=get_skill_service,
            get_user_grants=get_user_grants,
        )
    )

@asynccontextmanager
async def lifespan(_app: FastAPI):
    errors = validate_production_config()
    if errors:
        raise RuntimeError("生产配置校验失败：" + "；".join(errors))
    try:
        yield
    finally:
        shutdown_metrics_runtime()


app = FastAPI(title="Data Query Agent", version="0.1.0", lifespan=lifespan)
app.include_router(task_router)
app.include_router(semantic_router)
app.include_router(data_gateway_router)
app.include_router(model_operations_router)
app.include_router(integration_router)
app.include_router(skill_router)
app.include_router(mcp_registry_router)
app.include_router(reflection_rollout_router)
app.include_router(quality_admin_router)
app.include_router(proactive_analysis_router)
_cors_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["*"])

def _csrf_error(request: Request) -> Response:
    response = Response(content='{"detail":"CSRF token missing or invalid"}', status_code=403, media_type="application/json")
    origin = request.headers.get("origin")
    if origin and origin in _cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


@app.post("/api/v1/query/stream")
async def query_stream(
    request: QueryRequest,
    identity: dict = Depends(require_query_access),
    stream_version: str | None = Header(default=None, alias="X-Query-Stream-Version"),
    after_sequence: int | None = Query(default=None, ge=0),
):
    if stream_version == "2":
        if not request.client_request_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "E_QUERY_STREAM_ID_REQUIRED",
                    "message": "可恢复查询流必须提供 client_request_id",
                },
            )
        operation = "query.follow_up" if request.follow_up else "query"
        try:
            handle = await get_durable_query_stream_service().open(
                tenant_id=str(identity.get("tenant_id", "default")),
                owner_id=str(identity.get("subject", "")),
                operation=operation,
                client_request_id=request.client_request_id,
                run_query=lambda: query(request, identity),
                after_sequence=after_sequence,
            )
        except QueryStreamCursorError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        return StreamingResponse(
            handle.events,
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Accel-Buffering": "no",
                "X-Query-Stream-Id": handle.stream_id,
                "X-Query-Stream-Version": "2",
            },
        )
    events = stream_query_events(
        lambda: query(request, identity),
        chunk_rows=settings.query_stream_chunk_rows,
        heartbeat_seconds=settings.query_stream_heartbeat_seconds,
    )
    return StreamingResponse(
        events,
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    has_bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
    if settings.auth_enabled and not has_bearer and request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in {"/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"}:
        csrf_cookie = request.cookies.get("query_pilot_csrf")
        csrf_header = request.headers.get("X-CSRF-Token")
        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            return _csrf_error(request)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    # FastAPI Swagger UI loads its bundled assets from jsDelivr; keep the
    # allow-list narrow so the interactive API docs render without opening
    # arbitrary script sources.
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self' https://cdn.jsdelivr.net; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    return response


EXPORT_JOBS: dict[str, dict] = {}


def _cleanup_export_jobs() -> None:
    if settings.auth_enabled:
        try: db_delete_expired()
        except Exception: pass
    cutoff = time() - settings.export_job_ttl_seconds
    for job_id, job in list(EXPORT_JOBS.items()):
        if job.get("created_at", time()) < cutoff:
            path = Path(job.get("path", ""))
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
            EXPORT_JOBS.pop(job_id, None)


def _tenant_user(user_id: int, identity: dict) -> dict:
    """Return a target user only when it belongs to the caller's tenant."""
    user = get_user_by_id(user_id)
    if not user or user.get("tenant_id") != identity.get("tenant_id"):
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=20, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)

class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(default="", max_length=128)

class AdminUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(default="viewer", pattern="^(viewer|analyst|admin)$")

class AdminRoleRequest(BaseModel):
    role: str = Field(pattern="^(viewer|analyst|admin)$")

class AdminStatusRequest(BaseModel):
    active: bool

class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)

class GrantCreateRequest(BaseModel):
    datasource_id: str = "default"
    scope_column: str = Field(min_length=1, max_length=64)
    scope_value: str = Field(min_length=1, max_length=255)

class RolePolicyRequest(BaseModel):
    datasource_id: str = Field(min_length=1, max_length=128)
    role: str = Field(pattern="^(viewer|analyst|admin)$")
    tables: str | list[str] = "*"
    columns: str | dict[str, list[str]] = "*"
    mask: dict[str, str] = Field(default_factory=dict)


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie("query_pilot_refresh", token, httponly=True, secure=settings.auth_enabled, samesite="lax", max_age=14 * 24 * 3600, path="/api/v1/auth")
    response.set_cookie("query_pilot_csrf", secrets.token_urlsafe(32), httponly=False, secure=settings.auth_enabled, samesite="lax", max_age=14 * 24 * 3600, path="/")


@app.post("/api/v1/auth/login")
def login(request: LoginRequest, http_request: Request, response: Response):
    user = get_user(request.username)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user["locked_until"] and user["locked_until"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        raise HTTPException(status_code=423, detail="账号暂时锁定，请稍后重试")
    if not verify_password(request.password, user["password_hash"]):
        attempts = int(user["failed_attempts"] or 0) + 1
        lock = datetime.now(timezone.utc) + timedelta(minutes=15) if attempts >= 5 else None
        update_login_failure(user["id"], attempts, lock)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    clear_login_failures(user["id"])
    tokens = issue_tokens(subject=str(user["id"]), tenant_id=user["tenant_id"], roles=[user["role"]])
    client_ip = http_request.client.host if http_request.client else None
    record_session(tokens["session_id"], user["id"], refresh_token_hash(tokens["refresh_token"]), datetime.now(timezone.utc) + timedelta(days=14), ip_address=client_ip, user_agent=http_request.headers.get("user-agent"))
    _set_refresh_cookie(response, tokens["refresh_token"])
    return tokens


@app.post("/api/v1/auth/refresh")
def refresh(response: Response, request: RefreshRequest | None = None, cookie_token: str | None = Cookie(default=None, alias="query_pilot_refresh")):
    refresh_token = (request.refresh_token if request else None) or cookie_token
    if not refresh_token:
        raise HTTPException(status_code=401, detail="缺少 Refresh Token")
    try:
        claims = decode_refresh_claims(refresh_token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Refresh Token 无效或已过期") from exc
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Refresh Token 类型无效")
    session = get_session(claims.get("sid", ""), refresh_token_hash(refresh_token))
    if not session or session["revoked_at"] or session["expires_at"] <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(status_code=401, detail="会话无效或已撤销")
    revoke_session(claims["sid"])
    tokens = issue_tokens(subject=claims["sub"], tenant_id=claims["tenant_id"], roles=claims["roles"])
    record_session(tokens["session_id"], int(claims["sub"]), refresh_token_hash(tokens["refresh_token"]), datetime.now(timezone.utc) + timedelta(days=14), ip_address=session.get("ip_address"), user_agent=session.get("user_agent"))
    _set_refresh_cookie(response, tokens["refresh_token"])
    return tokens


@app.post("/api/v1/auth/logout")
def logout(response: Response, request: RefreshRequest | None = None, cookie_token: str | None = Cookie(default=None, alias="query_pilot_refresh")):
    refresh_token = (request.refresh_token if request else None) or cookie_token
    try:
        claims = decode_refresh_claims(refresh_token) if refresh_token else {}
        revoke_session(claims.get("sid", ""))
    except Exception:
        pass
    response.delete_cookie("query_pilot_refresh", path="/api/v1/auth")
    return {"status": "ok"}


@app.get("/api/v1/auth/me")
def current_user(identity: dict = Depends(require_identity)):
    if str(identity.get("subject", "")).isdigit():
        user = get_user_profile(int(identity["subject"]))
        if user:
            return {
                "subject": identity["subject"],
                "tenant_id": user["tenant_id"],
                "roles": [user["role"]],
                "sid": identity.get("sid"),
                "username": user["username"],
                "display_name": user.get("display_name") or "",
                "role": user["role"],
                "is_active": user["is_active"],
                "locked_until": user["locked_until"],
                "created_at": user["created_at"],
            }
    return identity


@app.patch("/api/v1/auth/profile")
def update_my_profile(request: ProfileUpdateRequest, identity: dict = Depends(require_identity)):
    subject = identity.get("subject", "")
    if not str(subject).isdigit():
        raise HTTPException(status_code=400, detail="当前身份不支持修改资料")
    display_name = request.display_name.strip()
    if len(display_name) > 128:
        raise HTTPException(status_code=422, detail="显示名称不能超过 128 个字符")
    update_profile(int(subject), display_name)
    return {"status": "ok", "display_name": display_name}


@app.get("/api/v1/auth/sessions")
def my_sessions(identity: dict = Depends(require_identity)):
    subject = identity.get("subject", "")
    if not str(subject).isdigit():
        return []
    current_sid = identity.get("sid")
    return [
        {**item, "current": item["id"] == current_sid}
        for item in list_sessions(int(subject))
    ]


@app.post("/api/v1/auth/sessions/{sid}/revoke")
def revoke_my_session(sid: str, identity: dict = Depends(require_identity)):
    subject = identity.get("subject", "")
    if not str(subject).isdigit():
        raise HTTPException(status_code=400, detail="当前身份不支持会话管理")
    revoke_session_by_id(int(subject), sid)
    return {"status": "ok"}


@app.post("/api/v1/auth/sessions/revoke-all")
def revoke_all_my_sessions(identity: dict = Depends(require_identity)):
    subject = identity.get("subject", "")
    if not str(subject).isdigit():
        raise HTTPException(status_code=400, detail="当前身份不支持会话管理")
    revoke_user_sessions(int(subject))
    return {"status": "ok"}


@app.get("/api/v1/admin/users")
def admin_users(identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    return list_users(identity["tenant_id"])

@app.post("/api/v1/admin/users")
def admin_create_user(request: AdminUserRequest, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    try: user_id = create_user(request.username, hash_password(request.password), request.role, identity["tenant_id"])
    except Exception as exc: raise HTTPException(status_code=409, detail="用户名已存在或创建失败") from exc
    return {"id": user_id, "username": request.username, "role": request.role}

@app.patch("/api/v1/admin/users/{user_id}/status")
def admin_status(user_id: int, request: AdminStatusRequest, identity: dict = Depends(require_identity)):
    authorize(identity, "manage"); set_user_status(user_id, identity["tenant_id"], request.active)
    if not request.active: revoke_user_sessions(user_id)
    return {"status": "ok"}

@app.patch("/api/v1/admin/users/{user_id}/role")
def admin_role(user_id: int, request: AdminRoleRequest, identity: dict = Depends(require_identity)):
    authorize(identity, "manage"); set_user_role(user_id, identity["tenant_id"], request.role); return {"status": "ok"}

@app.post("/api/v1/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, request: AdminResetPasswordRequest, identity: dict = Depends(require_identity)):
    authorize(identity, "manage"); _tenant_user(user_id, identity); update_password(user_id, hash_password(request.new_password)); revoke_user_sessions(user_id); return {"status": "ok"}

@app.delete("/api/v1/admin/users/{user_id}")
def admin_delete_user(user_id: int, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    if user_id == int(identity.get("subject", "-1")): raise HTTPException(status_code=400, detail="不能删除当前管理员账号")
    delete_user(user_id, identity["tenant_id"]); return {"status": "ok"}


@app.get("/api/v1/admin/users/{user_id}/grants")
def admin_user_grants(user_id: int, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    _tenant_user(user_id, identity)
    return get_user_grants(user_id)


@app.post("/api/v1/admin/users/{user_id}/grants")
def admin_create_grant(user_id: int, request: GrantCreateRequest, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    _tenant_user(user_id, identity)
    if not is_safe_scope_column(request.scope_column):
        raise HTTPException(status_code=400, detail="非法的字段名")
    datasource = get_datasource(request.datasource_id)
    if request.datasource_id == "default":
        datasource = datasource or get_datasource(settings.default_datasource)
    if not datasource:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="无权配置该数据源权限")
    try:
        available = datasource_schema(datasource)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据源 Schema 暂不可用") from exc
    if not any(request.scope_column in columns for columns in available.values()):
        raise HTTPException(status_code=400, detail="授权字段不存在于该数据源")
    grant_id = add_user_grant(user_id, request.datasource_id, request.scope_column, request.scope_value)
    return {"id": grant_id}


@app.delete("/api/v1/admin/grants/{grant_id}")
def admin_delete_grant(grant_id: int, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    delete_user_grant(grant_id, identity.get("tenant_id", "default"))
    return {"status": "ok"}


@app.get("/api/v1/admin/role-policies")
def admin_role_policies(datasource_id: str = "default", role: str = "viewer", identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    if role not in {"admin", "analyst", "viewer"}:
        raise HTTPException(status_code=400, detail="角色无效")
    datasource = get_datasource(datasource_id)
    if datasource_id == "default":
        datasource = datasource or get_datasource(settings.default_datasource)
    if not datasource:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="无权配置该数据源权限")
    return {"datasource_id": datasource_id, "role": role, **get_role_policy(datasource_id, role)}


@app.put("/api/v1/admin/role-policies")
def admin_update_role_policy(request: RolePolicyRequest, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    datasource = get_datasource(request.datasource_id)
    if request.datasource_id == "default":
        datasource = datasource or get_datasource(settings.default_datasource)
    if not datasource:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="无权配置该数据源权限")
    try:
        available = datasource_schema(datasource)
        saved = update_role_policy(request.datasource_id, request.role, request.model_dump(exclude={"datasource_id", "role"}), available)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="数据源 Schema 暂不可用") from exc
    return {"datasource_id": request.datasource_id, "role": request.role, **saved}


@app.post("/api/v1/auth/change-password")
def change_password(request: ChangePasswordRequest, identity: dict = Depends(require_identity)):
    if not str(identity.get("subject", "")).isdigit():
        raise HTTPException(status_code=400, detail="当前身份不支持修改密码")
    user = get_user_by_id(int(identity["subject"]))
    if not user or not verify_password(request.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="当前密码错误")
    update_password(user["id"], hash_password(request.new_password))
    revoke_user_sessions(user["id"])
    return {"status": "ok", "message": "密码已更新，请重新登录"}


@app.get("/")
def index():
    return {
        "name": "Data Query Agent",
        "version": app.version,
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
        "schema": "/api/v1/schema",
        "query": "POST /api/v1/query",
    }


def redis_status() -> dict:
    modes = []
    if settings.rate_limit_backend == "redis":
        modes.append("rate_limit")
    if settings.llm_circuit_backend == "redis":
        modes.append("llm_circuit")
    if settings.schema_embedding_cache_backend == "redis":
        modes.append("schema_embedding_cache")
    if settings.schema_embedding_circuit_backend == "redis":
        modes.append("schema_embedding_circuit")
    if settings.budget_backend == "redis":
        modes.append("budget")
    if settings.export_execution_mode == "redis":
        modes.append("export")
    if settings.agent_checkpoint_enabled:
        modes.append("checkpoint")
    if not modes:
        return {"required": False, "configured": False, "connected": None, "modes": []}
    status = {"required": True, "configured": bool(settings.redis_url.strip()), "connected": False, "modes": modes}
    if not status["configured"]:
        return status
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=False, socket_connect_timeout=0.25, socket_timeout=0.25)
        status["connected"] = bool(client.ping())
    except Exception:
        status["connected"] = False
    return status


def safe_database_status() -> dict:
    """Keep health probes alive when a driver/status adapter fails unexpectedly."""
    try:
        value = database_status()
        if isinstance(value, dict):
            return value
    except Exception:
        pass
    backend = settings.database_backend.lower()
    if backend == "postgres":
        backend = "postgresql"
    return {
        "backend": backend,
        "configured": False,
        "readonly": True,
        "connected": False,
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "database": safe_database_status(),
        "redis": redis_status(),
        "schema_embedding": schema_embedding_runtime_status(),
        "metrics": metrics_runtime_status(),
    }

@app.get("/ready")
def readiness():
    """Readiness probe: configured database and shared Redis dependencies respond."""
    db = safe_database_status()
    ready = bool(db.get("connected", db.get("ok", db.get("status") in {"ok", "healthy"})))
    redis = redis_status()
    ready = ready and (not redis["required"] or redis["connected"] is True)
    if not ready:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "database": db, "redis": redis})
    return {"status": "ready", "database": db, "redis": redis}

@app.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    """Expose write-time Prometheus metrics without scanning JSONL files."""
    if settings.metrics_token and request.headers.get("authorization") != f"Bearer {settings.metrics_token}":
        raise HTTPException(status_code=401, detail="指标访问凭证无效")
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/v1/schema")
def get_schema(identity: dict = Depends(require_identity)):
    # Keep direct Python callers (and older integrations) working while HTTP
    # requests always receive an injected identity from FastAPI.
    direct_call = not isinstance(identity, dict)
    if direct_call:
        identity = {"subject": "legacy-schema", "tenant_id": "default", "roles": ["admin"]}
    if direct_call and settings.database_backend.lower() == "mysql":
        return {table: sorted(columns) for table, columns in mysql_schema().items()}
    datasource = get_datasource(settings.default_datasource)
    configured_backend = settings.database_backend.lower()
    if datasource and configured_backend not in {"sqlite", datasource.get("backend", "sqlite").lower()}:
        datasource = next(
            (item for item in list_datasources() if item.get("backend", "sqlite").lower() == configured_backend),
            datasource,
        )
    if not datasource:
        raise HTTPException(status_code=404, detail={"code": "E_DATASOURCE_NOT_FOUND", "message": "默认数据源不存在"})
    if not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail={"code": "E_DATASOURCE_FORBIDDEN", "message": "无权访问默认数据源"})
    try:
        registry = build_gateway_registry(list_datasources())
        service = DataGatewayService(
            registry,
            can_use=lambda actor, descriptor: bool((configured := get_datasource(descriptor.id)) and role_can_use(actor, configured)),
            max_rows=settings.max_rows,
        )
        current = filter_schema(service.schema(identity, datasource["id"]), {**identity, "datasource": datasource["id"]})
        return {table: sorted(columns) for table, columns in current.items()}
    except GatewayError as exc:
        status = 403 if "FORBIDDEN" in exc.code else 502
        raise HTTPException(status_code=status, detail=exc.as_dict()) from exc


@app.get("/api/v1/retrieval")
def retrieval(question: str, limit: int = 3, identity: dict = Depends(require_identity)):
    context_service = QueryContextService(load_catalog=load_catalog, retrieve=retrieve_context, catalog_path=settings.catalog_path)
    return context_service.retrieve(question, min(limit, 10))

@app.get("/api/v1/observability/summary")
def observability_summary(identity: dict = Depends(require_identity)):
    return summarize(settings.audit_log_path, backup_count=settings.audit_log_backup_count)

@app.get("/api/v1/data-sources")
def data_sources(identity: dict = Depends(require_identity)):
    return list_data_sources_for(identity)


@app.get("/api/v1/data-sources/{ds_id}/schema")
def data_source_schema(ds_id: str, identity: dict = Depends(require_identity)):
    ds = get_datasource(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not role_can_use(identity, ds):
        raise HTTPException(status_code=403, detail="无权访问该数据源")
    try:
        current = datasource_schema(ds)
    except ValueError as exc:
        code, _, _message = str(exc).partition(":")
        raise HTTPException(status_code=422, detail={"code": code or "E_DATASOURCE_SCHEMA", "message": "数据源 Schema 暂时不可用"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "E_DATASOURCE_SCHEMA", "message": "数据源 Schema 检查失败"}) from exc
    return {table: sorted(columns) for table, columns in current.items()}


@app.post("/api/v1/data-sources/{ds_id}/test")
def data_source_test(ds_id: str, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    ds = get_datasource(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        return test_connection(ds)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "E_DATASOURCE_CONNECTION", "message": "数据源连接检查失败"}) from exc


@app.post("/api/v1/data-sources/{ds_id}/refresh")
def data_source_refresh(ds_id: str, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    ds = get_datasource(ds_id)
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        current = datasource_schema(ds)
    except ValueError as exc:
        code, _, _message = str(exc).partition(":")
        raise HTTPException(status_code=422, detail={"code": code or "E_DATASOURCE_SCHEMA", "message": "数据源 Schema 刷新失败"}) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "E_DATASOURCE_SCHEMA", "message": "数据源 Schema 刷新失败"}) from exc
    return {table: sorted(columns) for table, columns in current.items()}

@app.get("/api/v1/admin/config")
def admin_config(identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    return {"model": settings.model_name, "max_rows": settings.max_rows, "readonly": True}


@app.post("/api/v1/query")
async def query(request: QueryRequest, identity: dict = Depends(require_query_access)):
    return await get_query_application_service().execute(request, identity)


@app.get("/api/v1/approvals")
def list_approvals(identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    return db_list_approvals(identity["tenant_id"]) if settings.auth_enabled else approval.list_for(identity)


def _latest_turn_is_follow_up(task_service, task_id: str) -> bool:
    turns = [step for step in task_service.repository.list_steps(task_id) if step.kind == "understand_question"]
    if not turns:
        return False
    return bool(max(turns, key=lambda step: step.sequence).input.get("follow_up"))


def _fail_approval_task(item: dict, identity: dict, approval_id: str, error: str) -> None:
    task_id = item.get("task_id")
    if not task_id:
        return
    task_service = get_task_service()
    tenant_id = identity.get("tenant_id", "default")
    task = task_service.repository.get(task_id, tenant_id)
    if not task or task.status not in {"approval_required", "running", "verifying"}:
        return
    follow_up = _latest_turn_is_follow_up(task_service, task_id)
    task_service.resolve_approval_step(task_id=task_id, status="failed", output={"approval_id": approval_id}, error=error)
    if follow_up:
        task_service.restore_completed_after_follow_up(task_id, tenant_id, reason="follow_up_approval_failed")
        task_service.append_event(task_id=task_id, kind="task_follow_up_failed", payload={"approval_id": approval_id, "error_code": "E_APPROVAL_FAILED"})
    else:
        task_service.transition(task_id, tenant_id, "failed")
    task_service.append_event(task_id=task_id, kind="approval_failed", payload={"approval_id": approval_id, "error": error})


def _complete_approval_task(item: dict, identity: dict, approval_id: str, sql: str, columns: list, rows: list, truncated: bool) -> dict | None:
    task_id = item.get("task_id")
    if not task_id:
        return None
    task_service = get_task_service()
    tenant_id = identity.get("tenant_id", "default")
    task = task_service.repository.get(task_id, tenant_id)
    if not task or task.status != "approval_required":
        raise ValueError("E_APPROVAL_TASK_STATE:关联任务不存在或状态不允许审批完成")
    steps = task_service.repository.list_steps(task_id)
    follow_up = _latest_turn_is_follow_up(task_service, task_id)
    sequence = max((step.sequence for step in steps), default=-1) + 1
    task_service.resolve_approval_step(task_id=task_id, status="completed", output={"approval_id": approval_id, "approver_id": str(identity.get("subject", ""))})
    task_service.transition(task_id, tenant_id, "running")
    task_service.append_step(task_id=task_id, kind="query", sequence=sequence, status="completed", output={"sql": sql, "columns": columns, "row_count": len(rows), "approval_id": approval_id, "tool_replay": get_tool_replay_policy_service().require("query.execute").as_dict()})
    task_service.transition(task_id, tenant_id, "verifying")
    task_service.append_step(task_id=task_id, kind="verify_result", sequence=sequence + 1, status="completed", output={"accepted": True, "row_count": len(rows), "truncated": truncated, "method": "approval_execution"})
    next_sequence = sequence + 2
    analysis = None
    try:
        analysis_mode = AnalysisMode(item.get("analysis_mode", "none"))
    except ValueError:
        analysis_mode = AnalysisMode.NONE
    if analysis_mode != AnalysisMode.NONE:
        analysis = describe_result((columns, rows, truncated), {"analysis_mode": analysis_mode.value}).model_dump()
        task_service.append_step(task_id=task_id, kind="analyze_result", sequence=next_sequence, status="completed", output=analysis)
        next_sequence += 1
        task_service.append_step(task_id=task_id, kind="explain_result", sequence=next_sequence, status="completed", output={"summary": analysis["summary"], "row_count": len(rows), "truncated": truncated, "method": "deterministic"})
        next_sequence += 1
    preview = build_result_preview(
        columns,
        rows,
        source_truncated=truncated,
        extra_metadata={"data_scope": "redacted"},
    )
    artifact_key = f"{task_id}:table:approval-result:{approval_id}" if follow_up else f"{task_id}:table:approval-result"
    task_service.ensure_artifact(task_id=task_id, kind="table", name="审批查询结果", idempotency_key=artifact_key, metadata=preview)
    artifact_keys = [artifact_key]
    chart_artifact = build_chart_artifact_definition(
        preview,
        source_idempotency_key=artifact_key,
        chart_idempotency_key=f"{artifact_key}:chart",
    )
    if chart_artifact:
        task_service.ensure_artifact(task_id=task_id, kind=chart_artifact["kind"], name=chart_artifact["name"], idempotency_key=chart_artifact["idempotency_key"], metadata=chart_artifact["metadata"])
        artifact_keys.append(chart_artifact["idempotency_key"])
    task_service.append_step(task_id=task_id, kind="materialize_artifacts", sequence=next_sequence, status="completed", output={"count": len(artifact_keys), "idempotency_key": artifact_key, "idempotency_keys": artifact_keys, "tool_replay": get_tool_replay_policy_service().require("result.materialize").as_dict()})
    task_service.transition(task_id, tenant_id, "completed")
    task_service.append_event(task_id=task_id, kind="approval_completed", payload={"approval_id": approval_id, "row_count": len(rows)})
    return analysis


def _approval_requester_identity(item: dict) -> dict:
    requester_id = str(item.get("requester_id", ""))
    tenant_id = item.get("tenant_id")
    if settings.auth_enabled:
        if not requester_id.isdigit():
            raise PermissionError("审批单缺少有效申请人")
        user = get_user_by_id(int(requester_id))
        if not user or not user.get("is_active") or user.get("tenant_id") != tenant_id:
            raise PermissionError("审批申请人不存在、已停用或租户不匹配")
        roles = [user["role"]]
    else:
        roles = list(item.get("requester_roles") or [])
    return {"subject": requester_id, "tenant_id": tenant_id, "roles": roles}


@app.post("/api/v1/approvals/{approval_id}/reject")
def reject_approval(approval_id: str, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    item = db_get_approval(approval_id, identity["tenant_id"]) if settings.auth_enabled else approval.get_for(approval_id, identity)
    if not item:
        raise HTTPException(status_code=404, detail="审批单不存在")
    if item["status"] != "pending":
        raise HTTPException(status_code=409, detail="审批单已处理或已过期")
    item["status"] = "rejected"
    if settings.auth_enabled and not db_set_approval_status(approval_id, identity["tenant_id"], "rejected"):
        raise HTTPException(status_code=409, detail="审批单已被其他管理员处理")
    _fail_approval_task(item, identity, approval_id, "审批已拒绝")
    write_audit(settings.audit_log_path, trace_id=approval_id, task_id=item.get("task_id"), tenant_id=item.get("tenant_id"), sql=item.get("sql", ""), duration_ms=0, event="approval_rejected", actor_id=str(identity.get("subject")), approval_id=approval_id)
    return {"id": approval_id, "status": item["status"]}


@app.post("/api/v1/approvals/{approval_id}/approve")
def approve_approval(approval_id: str, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    item = db_get_approval(approval_id, identity["tenant_id"]) if settings.auth_enabled else approval.get_for(approval_id, identity)
    if not item:
        raise HTTPException(status_code=404, detail="审批单不存在")
    if item["status"] != "pending":
        raise HTTPException(status_code=409, detail="审批单已处理或已过期")
    if settings.auth_enabled:
        if not db_set_approval_status(approval_id, identity["tenant_id"], "processing"):
            raise HTTPException(status_code=409, detail="审批单已被其他管理员处理")
    else:
        item["status"] = "processing"
    datasource = get_datasource(item["datasource_id"])
    if not datasource or not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="无权访问该数据源")
    try:
        requester_identity = _approval_requester_identity(item)
    except PermissionError as exc:
        item["status"] = "failed"
        if settings.auth_enabled: db_set_approval_status(approval_id, identity["tenant_id"], "failed", "processing")
        _fail_approval_task(item, identity, approval_id, str(exc))
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not role_can_use(requester_identity, datasource):
        item["status"] = "failed"
        if settings.auth_enabled: db_set_approval_status(approval_id, identity["tenant_id"], "failed", "processing")
        _fail_approval_task(item, identity, approval_id, "申请人已无权访问该数据源")
        raise HTTPException(status_code=403, detail="申请人已无权访问该数据源")
    current_schema = datasource_schema(datasource)
    policy_identity = {**requester_identity, "datasource": datasource["id"]}
    approval_cost_estimate = None
    try:
        sql = apply_policy(validate_sql(item["sql"], current_schema, settings.max_sql_length, dialect=datasource.get("backend", "sqlite"), max_params=settings.max_params), policy_identity)
        if settings.query_cost_guard_enabled:
            cost_registry = build_gateway_registry(list_datasources())
            cost_service = DataGatewayService(cost_registry, can_use=lambda actor, descriptor: bool((configured := get_datasource(descriptor.id)) and role_can_use(actor, configured)), max_rows=settings.max_rows)
            approval_cost_estimate = cost_service.estimate_cost(requester_identity, datasource["id"], GatewayQuery(sql=sql, params=item.get("params") or {}, max_rows=settings.max_rows))
            decision = evaluate_query_cost(approval_cost_estimate, max_estimated_rows=settings.query_cost_max_estimated_rows, max_estimated_cost=settings.query_cost_max_estimated_cost, require_explain=settings.query_cost_require_explain)
            if not decision.allowed:
                raise QueryError("E_QUERY_COST_LIMIT", "审批重新执行前的查询成本评估仍未通过")
        columns, rows, truncated = execute_datasource(datasource, sql, item["params"], settings.max_rows, 0)
        rows = redact_rows(columns, rows, policy_identity)
    except QueryError as exc:
        item["status"] = "failed"
        if settings.auth_enabled: db_set_approval_status(approval_id, identity["tenant_id"], "failed", "processing")
        _fail_approval_task(item, identity, approval_id, str(exc))
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})
    except Exception as exc:
        item["status"] = "failed"
        if settings.auth_enabled: db_set_approval_status(approval_id, identity["tenant_id"], "failed", "processing")
        _fail_approval_task(item, identity, approval_id, str(exc))
        raise HTTPException(status_code=502, detail=f"审批执行失败：{exc}")
    try:
        completed_analysis = _complete_approval_task(item, identity, approval_id, sql, columns, rows, truncated)
    except Exception as exc:
        item["status"] = "failed"
        if settings.auth_enabled: db_set_approval_status(approval_id, identity["tenant_id"], "failed", "processing")
        _fail_approval_task(item, identity, approval_id, str(exc))
        raise HTTPException(status_code=500, detail="审批已执行，但任务状态同步失败") from exc
    item["status"] = "approved"
    if settings.auth_enabled: db_set_approval_status(approval_id, identity["tenant_id"], "approved", "processing")
    write_audit(settings.audit_log_path, trace_id=approval_id, task_id=item.get("task_id"), tenant_id=item.get("tenant_id"), sql=sql, duration_ms=0, row_count=len(rows), truncated=truncated, large_result=len(rows) >= settings.large_result_row_threshold, event="approval_approved", actor_id=str(identity.get("subject")), approval_id=approval_id, cost_backend=getattr(approval_cost_estimate, "backend", None), cost_supported=getattr(approval_cost_estimate, "supported", None), estimated_rows=getattr(approval_cost_estimate, "estimated_rows", None), estimated_cost=getattr(approval_cost_estimate, "estimated_cost", None), full_scan=getattr(approval_cost_estimate, "full_scan", None), index_used=getattr(approval_cost_estimate, "index_used", None))
    return {"approval_id": approval_id, "status": item["status"], "sql": sql, "columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated, "analysis": completed_analysis}


@app.get("/api/v1/sessions")
def sessions(identity: dict = Depends(require_identity)):
    if not str(identity.get("subject", "")).isdigit(): return []
    return list_queries(int(identity["subject"]), identity["tenant_id"])


@app.delete("/api/v1/sessions/{session_id}")
def delete_session(session_id: str, identity: dict = Depends(require_identity)):
    if str(identity.get("subject", "")).isdigit(): delete_query(int(identity["subject"]), identity["tenant_id"], session_id)
    return {"status": "ok"}

def _run_export(job_id: str, sql: str, params: dict, datasource: dict):
    try:
        columns, rows, _ = execute_datasource(datasource, sql, params, settings.max_rows, 0)
        path, count = write_csv(columns, rows, settings.export_dir)
        EXPORT_JOBS[job_id] = {**EXPORT_JOBS.get(job_id, {}), "status": "completed", "path": path, "row_count": count}
        if settings.auth_enabled and EXPORT_JOBS[job_id].get("persisted"):
            try: db_update_export(job_id, status="completed", file_path=path, row_count=count)
            except Exception: pass
    except Exception as exc:
        EXPORT_JOBS[job_id] = {**EXPORT_JOBS.get(job_id, {}), "status": "failed", "error": str(exc)}
        if settings.auth_enabled and EXPORT_JOBS.get(job_id, {}).get("persisted"):
            try: db_update_export(job_id, status="failed", error_message=str(exc))
            except Exception: pass

@app.post("/api/v1/export")
async def create_export(request: QueryRequest, background_tasks: BackgroundTasks, identity: dict = Depends(require_export_access)):
    _cleanup_export_jobs()
    datasource = get_datasource(request.datasource or settings.default_datasource)
    if not datasource or not role_can_use(identity, datasource):
        raise HTTPException(status_code=403, detail="无权访问该数据源")
    current_schema = datasource_schema(datasource)
    sql = apply_policy(
        validate_sql(request.sql or "", current_schema, settings.max_sql_length, dialect=datasource.get("backend", "sqlite"), max_params=settings.max_params),
        {**identity, "datasource": datasource["id"]},
    )
    replay_policy = get_tool_replay_policy_service().require("export.enqueue").as_dict()
    job_id = str(uuid4()); EXPORT_JOBS[job_id] = {"status": "queued", "created_at": time(), "user_id": int(identity["subject"]) if str(identity.get("subject", "")).isdigit() else None, "tenant_id": identity.get("tenant_id"), "persisted": False, "replay_policy": replay_policy}
    if settings.auth_enabled:
        try:
            db_create_export(job_id, EXPORT_JOBS[job_id]["user_id"], identity.get("tenant_id"), datasource["id"], sql, request.params, datetime.now(timezone.utc) + timedelta(seconds=settings.export_job_ttl_seconds))
            EXPORT_JOBS[job_id]["persisted"] = True
        except Exception:
            pass
    if settings.export_execution_mode == "redis":
        if not EXPORT_JOBS[job_id].get("persisted"):
            raise HTTPException(status_code=503, detail="Redis 导出模式需要可用的任务数据库")
        try:
            enqueue_export(settings.redis_url, job_id)
        except Exception as exc:
            try: db_update_export(job_id, status="failed", error_message=str(exc))
            except Exception: pass
            raise HTTPException(status_code=503, detail="导出队列不可用")
    else:
        background_tasks.add_task(_run_export, job_id, sql, request.params, datasource)
    return {"job_id": job_id, "status": "queued", "replay_policy": replay_policy}

@app.get("/api/v1/export/{job_id}")
def export_status(job_id: str, identity: dict = Depends(require_identity)):
    memory_job = EXPORT_JOBS.get(job_id)
    job = (db_get_export(job_id, int(identity["subject"]) if str(identity.get("subject", "")).isdigit() else None, identity.get("tenant_id")) if settings.auth_enabled and not (memory_job and not memory_job.get("persisted")) else memory_job)
    subject = identity.get("subject", "")
    if not job or job.get("tenant_id") != identity.get("tenant_id") or (job.get("user_id") is not None and str(subject).isdigit() and job.get("user_id") != int(subject)):
        raise HTTPException(404, "导出任务不存在")
    return {**job, "replay_policy": get_tool_replay_policy_service().require("export.enqueue").as_dict()}


@app.get("/api/v1/export/{job_id}/download")
def export_download(job_id: str, identity: dict = Depends(require_identity)):
    memory_job = EXPORT_JOBS.get(job_id)
    job = (db_get_export(job_id, int(identity["subject"]) if str(identity.get("subject", "")).isdigit() else None, identity.get("tenant_id")) if settings.auth_enabled and not (memory_job and not memory_job.get("persisted")) else memory_job)
    subject = identity.get("subject", "")
    if not job or job.get("tenant_id") != identity.get("tenant_id") or (job.get("user_id") is not None and str(subject).isdigit() and job.get("user_id") != int(subject)) or job.get("status") != "completed":
        raise HTTPException(404, "导出任务不存在或未完成")
    path = Path(job.get("path", ""))
    if not path.is_file():
        raise HTTPException(404, "导出文件不存在")
    return FileResponse(path, filename=path.name)

@app.get("/api/v1/admin/export-dead-letters")
def export_dead_letters(limit: int = 100, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    if settings.export_execution_mode != "redis":
        return []
    try:
        return list_dead_letters(settings.redis_url, min(max(limit, 1), 500))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="死信队列不可用") from exc

@app.post("/api/v1/admin/export-dead-letters/{job_id}/retry")
def retry_export_dead_letter(job_id: str, identity: dict = Depends(require_identity)):
    authorize(identity, "manage")
    if settings.export_execution_mode != "redis":
        raise HTTPException(status_code=409, detail="当前未启用 Redis 导出模式")
    try:
        if not retry_dead_letter(settings.redis_url, job_id):
            raise HTTPException(status_code=404, detail="死信任务不存在")
        db_update_export(job_id, status="queued", error_message="管理员重新入队")
        return {"status": "queued", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="死信队列不可用") from exc
