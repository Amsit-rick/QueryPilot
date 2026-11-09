"""MySQL persistence adapter for governed external MCP servers."""

from __future__ import annotations

import json

from app.domain.mcp_registry import McpServerRegistration
from app.mysql_db import connect


class MySQLMcpRegistryRepository:
    _columns = (
        "id,tenant_id,name,transport,endpoint,auth_env_ref,allowed_tools_json,"
        "enabled,read_only_only,allow_private_network,request_timeout_seconds,max_concurrency,"
        "daily_call_limit,circuit_failure_threshold,circuit_reset_seconds,health_status,"
        "last_latency_ms,last_error_code,discovered_tools_json,last_checked_at,created_at,updated_at"
    )

    @staticmethod
    def _server(row) -> McpServerRegistration:
        allowed_tools = json.loads(row[6]) if row[6] else []
        discovered_tools = json.loads(row[18]) if row[18] else []
        return McpServerRegistration(
            id=row[0], tenant_id=row[1], name=row[2], transport=row[3], endpoint=row[4],
            auth_env_ref=row[5], allowed_tools=tuple(str(item) for item in allowed_tools),
            enabled=bool(row[7]), read_only_only=bool(row[8]), allow_private_network=bool(row[9]),
            request_timeout_seconds=float(row[10]), max_concurrency=int(row[11]),
            daily_call_limit=int(row[12]), circuit_failure_threshold=int(row[13]),
            circuit_reset_seconds=int(row[14]), health_status=row[15], last_latency_ms=int(row[16]),
            last_error_code=row[17] or "", discovered_tools=tuple(str(item) for item in discovered_tools),
            last_checked_at=row[19], created_at=row[20], updated_at=row[21],
        )

    def create(self, server: McpServerRegistration) -> McpServerRegistration:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO mcp_server_registry "
                    "(id,tenant_id,name,transport,endpoint,auth_env_ref,allowed_tools_json,enabled,read_only_only,"
                    "allow_private_network,request_timeout_seconds,max_concurrency,daily_call_limit,circuit_failure_threshold,"
                    "circuit_reset_seconds,health_status,last_latency_ms,last_error_code,discovered_tools_json,last_checked_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        server.id, server.tenant_id, server.name, server.transport, server.endpoint,
                        server.auth_env_ref, json.dumps(server.allowed_tools, ensure_ascii=True), server.enabled,
                        server.read_only_only, server.allow_private_network, server.request_timeout_seconds,
                        server.max_concurrency, server.daily_call_limit, server.circuit_failure_threshold,
                        server.circuit_reset_seconds, server.health_status, server.last_latency_ms,
                        server.last_error_code or None, json.dumps(server.discovered_tools, ensure_ascii=True),
                        server.last_checked_at.replace(tzinfo=None) if server.last_checked_at else None,
                        server.created_at.replace(tzinfo=None), server.updated_at.replace(tzinfo=None),
                    ),
                )
            conn.commit()
        return server

    def update(self, server: McpServerRegistration) -> McpServerRegistration:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE mcp_server_registry SET name=%s,transport=%s,endpoint=%s,auth_env_ref=%s,"
                    "allowed_tools_json=%s,enabled=%s,read_only_only=%s,allow_private_network=%s,"
                    "request_timeout_seconds=%s,max_concurrency=%s,daily_call_limit=%s,circuit_failure_threshold=%s,"
                    "circuit_reset_seconds=%s,health_status=%s,last_latency_ms=%s,last_error_code=%s,"
                    "discovered_tools_json=%s,last_checked_at=%s,updated_at=%s WHERE id=%s AND tenant_id=%s",
                    (
                        server.name, server.transport, server.endpoint, server.auth_env_ref,
                        json.dumps(server.allowed_tools, ensure_ascii=True), server.enabled, server.read_only_only,
                        server.allow_private_network, server.request_timeout_seconds, server.max_concurrency,
                        server.daily_call_limit, server.circuit_failure_threshold, server.circuit_reset_seconds,
                        server.health_status, server.last_latency_ms, server.last_error_code or None,
                        json.dumps(server.discovered_tools, ensure_ascii=True),
                        server.last_checked_at.replace(tzinfo=None) if server.last_checked_at else None,
                        server.updated_at.replace(tzinfo=None), server.id, server.tenant_id,
                    ),
                )
            conn.commit()
        return server

    def get(self, server_id: str, tenant_id: str) -> McpServerRegistration | None:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {self._columns} FROM mcp_server_registry WHERE id=%s AND tenant_id=%s", (server_id, tenant_id))
                row = cur.fetchone()
        return self._server(row) if row else None

    def list(self, tenant_id: str) -> list[McpServerRegistration]:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {self._columns} FROM mcp_server_registry WHERE tenant_id=%s ORDER BY name", (tenant_id,))
                rows = cur.fetchall()
        return [self._server(row) for row in rows]

    def delete(self, server_id: str, tenant_id: str) -> bool:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mcp_server_registry WHERE id=%s AND tenant_id=%s", (server_id, tenant_id))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted
