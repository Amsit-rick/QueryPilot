"""MySQL persistence adapter for governed external MCP servers."""

from __future__ import annotations

import json

from app.domain.mcp_registry import McpServerRegistration
from app.mysql_db import connect


class MySQLMcpRegistryRepository:
    _columns = (
        "id,tenant_id,name,transport,endpoint,auth_env_ref,auth_type,oauth_token_url,oauth_client_id,"
        "oauth_scopes_json,credential_version,credential_rotated_at,allowed_tools_json,"
        "enabled,read_only_only,allow_private_network,request_timeout_seconds,max_concurrency,"
        "daily_call_limit,circuit_failure_threshold,circuit_reset_seconds,health_status,"
        "last_latency_ms,last_error_code,discovered_tools_json,last_checked_at,created_at,updated_at"
    )

    @staticmethod
    def _server(row) -> McpServerRegistration:
        oauth_scopes = json.loads(row[9]) if row[9] else []
        allowed_tools = json.loads(row[12]) if row[12] else []
        discovered_tools = json.loads(row[24]) if row[24] else []
        return McpServerRegistration(
            id=row[0], tenant_id=row[1], name=row[2], transport=row[3], endpoint=row[4],
            auth_env_ref=row[5], auth_type=row[6] or "bearer", oauth_token_url=row[7] or "",
            oauth_client_id=row[8] or "", oauth_scopes=tuple(str(item) for item in oauth_scopes),
            credential_version=int(row[10]), credential_rotated_at=row[11],
            allowed_tools=tuple(str(item) for item in allowed_tools),
            enabled=bool(row[13]), read_only_only=bool(row[14]), allow_private_network=bool(row[15]),
            request_timeout_seconds=float(row[16]), max_concurrency=int(row[17]),
            daily_call_limit=int(row[18]), circuit_failure_threshold=int(row[19]),
            circuit_reset_seconds=int(row[20]), health_status=row[21], last_latency_ms=int(row[22]),
            last_error_code=row[23] or "", discovered_tools=tuple(str(item) for item in discovered_tools),
            last_checked_at=row[25], created_at=row[26], updated_at=row[27],
        )

    def create(self, server: McpServerRegistration) -> McpServerRegistration:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO mcp_server_registry "
                    "(id,tenant_id,name,transport,endpoint,auth_env_ref,auth_type,oauth_token_url,oauth_client_id,"
                    "oauth_scopes_json,credential_version,credential_rotated_at,allowed_tools_json,enabled,read_only_only,"
                    "allow_private_network,request_timeout_seconds,max_concurrency,daily_call_limit,circuit_failure_threshold,"
                    "circuit_reset_seconds,health_status,last_latency_ms,last_error_code,discovered_tools_json,last_checked_at,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        server.id, server.tenant_id, server.name, server.transport, server.endpoint,
                        server.auth_env_ref, server.auth_type, server.oauth_token_url or None,
                        server.oauth_client_id or None, json.dumps(server.oauth_scopes, ensure_ascii=True),
                        server.credential_version,
                        server.credential_rotated_at.replace(tzinfo=None) if server.credential_rotated_at else None,
                        json.dumps(server.allowed_tools, ensure_ascii=True), server.enabled,
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
                    "UPDATE mcp_server_registry SET name=%s,transport=%s,endpoint=%s,auth_env_ref=%s,auth_type=%s,"
                    "oauth_token_url=%s,oauth_client_id=%s,oauth_scopes_json=%s,credential_version=%s,credential_rotated_at=%s,"
                    "allowed_tools_json=%s,enabled=%s,read_only_only=%s,allow_private_network=%s,"
                    "request_timeout_seconds=%s,max_concurrency=%s,daily_call_limit=%s,circuit_failure_threshold=%s,"
                    "circuit_reset_seconds=%s,health_status=%s,last_latency_ms=%s,last_error_code=%s,"
                    "discovered_tools_json=%s,last_checked_at=%s,updated_at=%s WHERE id=%s AND tenant_id=%s",
                    (
                        server.name, server.transport, server.endpoint, server.auth_env_ref, server.auth_type,
                        server.oauth_token_url or None, server.oauth_client_id or None,
                        json.dumps(server.oauth_scopes, ensure_ascii=True), server.credential_version,
                        server.credential_rotated_at.replace(tzinfo=None) if server.credential_rotated_at else None,
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
