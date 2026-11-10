"""Authenticated, read-only integration tools.

The service is deliberately usable without an HTTP request so an embedded
adapter can expose the same contract. It never executes a query: planning
only validates a caller-supplied SELECT through the existing policy boundary.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.audit import write_audit
from app.config import settings
from app.datasources import datasource_schema, datasource_schema_details, get_datasource, list_datasources, role_can_use
from app.gateways.factory import build_gateway_registry
from app.guardrails import QueryError, validate_sql
from app.policy import apply_policy, filter_schema
from app.retrieval import load_catalog
from app.services.relationship_service import resolve_relationships
from app.services.data_gateway_service import DataGatewayService
from app.task_runtime import get_task_service


class ReadOnlyIntegrationError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "schema.list",
        "description": "列出当前身份在指定只读数据源中可见的表和字段。",
        "read_only": True,
    },
    {
        "name": "query.plan",
        "description": "校验只读查询计划；不会执行 SQL，也不会绕过审批。",
        "read_only": True,
    },
    {
        "name": "task.status",
        "description": "读取当前身份拥有的任务状态和最近事件。",
        "read_only": True,
    },
    {
        "name": "result.get",
        "description": "读取当前身份拥有任务的持久化结果资产和受限预览。",
        "read_only": True,
    },
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _gateway_service() -> DataGatewayService:
    return DataGatewayService(
        build_gateway_registry(list_datasources()),
        can_use=lambda identity, descriptor: bool(
            (configured := get_datasource(descriptor.id))
            and role_can_use(identity, configured)
        ),
        max_rows=settings.max_rows,
    )


class ReadOnlyIntegrationService:
    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in TOOLS]

    def invoke(self, identity: dict[str, Any], tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        trace_id = str(uuid4())
        started = perf_counter()
        handler = {
            "schema.list": self._schema_list,
            "query.plan": self._query_plan,
            "task.status": self._task_status,
            "result.get": self._result_get,
        }.get(tool)
        if handler is None:
            raise ReadOnlyIntegrationError("E_INTEGRATION_TOOL_NOT_FOUND", "只读工具不存在", status_code=404)
        task_id = str(arguments.get("task_id", "")) or None
        try:
            result = handler(identity, arguments)
        except ReadOnlyIntegrationError:
            raise
        except QueryError as exc:
            raise ReadOnlyIntegrationError(exc.code, str(exc), status_code=422) from exc
        finally:
            write_audit(
                settings.audit_log_path,
                trace_id=trace_id,
                task_id=task_id,
                tenant_id=identity.get("tenant_id", "default"),
                sql=f"integration:{tool}",
                duration_ms=int((perf_counter() - started) * 1000),
                event="integration_tool_call",
                actor_id=str(identity.get("subject", "")),
            )
        return {"version": 1, "trace_id": trace_id, "tool": tool, "read_only": True, "result": result}

    @staticmethod
    def _datasource_id(arguments: dict[str, Any]) -> str:
        value = arguments.get("datasource_id") or settings.default_datasource
        if not isinstance(value, str) or not value.strip():
            raise ReadOnlyIntegrationError("E_INTEGRATION_DATASOURCE", "datasource_id 不能为空")
        return value.strip()

    @staticmethod
    def _authorized_datasource(identity: dict[str, Any], datasource_id: str) -> dict[str, Any]:
        datasource = get_datasource(datasource_id)
        if not datasource:
            raise ReadOnlyIntegrationError("E_DATASOURCE_NOT_FOUND", "数据源不存在", status_code=404)
        if not role_can_use(identity, datasource):
            raise ReadOnlyIntegrationError("E_DATASOURCE_FORBIDDEN", "无权访问该数据源", status_code=403)
        return datasource

    def _schema_list(self, identity: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        datasource_id = self._datasource_id(arguments)
        self._authorized_datasource(identity, datasource_id)
        visible = filter_schema(
            datasource_schema(get_datasource(datasource_id)),
            {**identity, "datasource": datasource_id},
        )
        details = datasource_schema_details(get_datasource(datasource_id))
        database = [relation if isinstance(relation, dict) else relation.__dict__ for relation in details.get("relationships", [])]
        relationships = resolve_relationships(visible, database=database, manual=load_catalog(settings.catalog_path).get("relationships", []), datasource_id=datasource_id, tenant_id=identity.get("tenant_id"))
        return {
            "datasource_id": datasource_id,
            "tables": {table: sorted(columns) for table, columns in sorted(visible.items())},
            "relationships": relationships,
        }

    def _query_plan(self, identity: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        question = arguments.get("question", "")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ReadOnlyIntegrationError("E_INTEGRATION_QUESTION", "question 必须为 1 到 2000 个字符")
        datasource_id = self._datasource_id(arguments)
        self._authorized_datasource(identity, datasource_id)
        schema = filter_schema(
            datasource_schema(get_datasource(datasource_id)),
            {**identity, "datasource": datasource_id},
        )
        sql = arguments.get("sql")
        plan: dict[str, Any] = {
            "question": question.strip(),
            "datasource_id": datasource_id,
            "read_only": True,
            "execution_delegated": True,
            "requires_approval": bool(arguments.get("require_approval", False)),
            "sql_provided": bool(isinstance(sql, str) and sql.strip()),
            "status": "ready",
        }
        if sql is None or (isinstance(sql, str) and not sql.strip()):
            plan["validation"] = "deferred_to_query_execution"
            return plan
        if not isinstance(sql, str):
            raise ReadOnlyIntegrationError("E_INTEGRATION_SQL", "sql 必须是字符串")
        backend = get_datasource(datasource_id).get("backend", "sqlite")
        normalized = validate_sql(sql, {table: set(columns) for table, columns in schema.items()}, settings.max_sql_length, dialect=backend, max_params=settings.max_params)
        plan["sql"] = apply_policy(normalized, {**identity, "datasource": datasource_id}, dialect=backend)
        plan["validation"] = "passed"
        return plan

    @staticmethod
    def _task(identity: dict[str, Any], task_id: Any) -> dict[str, Any]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ReadOnlyIntegrationError("E_INTEGRATION_TASK", "task_id 不能为空")
        try:
            return get_task_service().detail(
                task_id=task_id.strip(),
                tenant_id=identity.get("tenant_id", "default"),
                owner_id=str(identity.get("subject", "")),
            )
        except LookupError as exc:
            raise ReadOnlyIntegrationError("E_TASK_NOT_FOUND", "任务不存在或无权访问", status_code=404) from exc

    def _task_status(self, identity: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        detail = self._task(identity, arguments.get("task_id"))
        task = detail["task"]
        events = detail["events"]
        latest_event = events[-1] if events else None
        return {
            "task_id": task.id,
            "status": task.status,
            "question": task.question,
            "datasource_id": task.datasource_id,
            "updated_at": _iso(task.updated_at),
            "latest_event": {
                "kind": latest_event.kind,
                "created_at": _iso(latest_event.created_at),
            }
            if latest_event
            else None,
        }

    def _result_get(self, identity: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        detail = self._task(identity, arguments.get("task_id"))
        task = detail["task"]
        return {
            "task_id": task.id,
            "status": task.status,
            "artifacts": [
                {
                    "id": artifact.id,
                    "kind": artifact.kind,
                    "name": artifact.name,
                    "created_at": _iso(artifact.created_at),
                    "metadata": artifact.metadata,
                }
                for artifact in detail["artifacts"]
            ],
        }


_service = ReadOnlyIntegrationService()


def get_read_only_integration_service() -> ReadOnlyIntegrationService:
    return _service
