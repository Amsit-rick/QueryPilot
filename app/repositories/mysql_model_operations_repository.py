from __future__ import annotations

from app.domain.model_operations import ModelProvider, PromptTemplate
from app.mysql_db import connect


class MySQLModelOperationsRepository:
    _provider_columns = "id,tenant_id,name,adapter,base_url,model_name,api_key_env,enabled,is_default,health_status,last_latency_ms,last_error_code,last_checked_at,created_at,updated_at"
    _prompt_columns = "id,tenant_id,name,system_prompt,version,owner_id,active,created_at"

    @staticmethod
    def _provider(row):
        return ModelProvider(id=row[0], tenant_id=row[1], name=row[2], adapter=row[3], base_url=row[4], model=row[5], api_key_env=row[6], enabled=bool(row[7]), is_default=bool(row[8]), health_status=row[9], last_latency_ms=row[10], last_error_code=row[11] or "", last_checked_at=row[12], created_at=row[13], updated_at=row[14])

    @staticmethod
    def _prompt(row):
        return PromptTemplate(id=row[0], tenant_id=row[1], name=row[2], system_prompt=row[3], version=row[4], owner_id=row[5] or "", active=bool(row[6]), created_at=row[7])

    def create_provider(self, provider):
        with connect() as conn:
            with conn.cursor() as cur:
                if provider.is_default:
                    cur.execute("UPDATE model_providers SET is_default=FALSE WHERE tenant_id=%s", (provider.tenant_id,))
                cur.execute("INSERT INTO model_providers (id,tenant_id,name,adapter,base_url,model_name,api_key_env,enabled,is_default,health_status,last_latency_ms,last_error_code,last_checked_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (provider.id, provider.tenant_id, provider.name, provider.adapter, provider.base_url, provider.model, provider.api_key_env, provider.enabled, provider.is_default, provider.health_status, provider.last_latency_ms, provider.last_error_code or None, provider.last_checked_at, provider.created_at.replace(tzinfo=None), provider.updated_at.replace(tzinfo=None)))
            conn.commit()
        return provider

    def update_provider(self, provider):
        with connect() as conn:
            with conn.cursor() as cur:
                if provider.is_default:
                    cur.execute("UPDATE model_providers SET is_default=FALSE WHERE tenant_id=%s AND id<>%s", (provider.tenant_id, provider.id))
                cur.execute("UPDATE model_providers SET name=%s,adapter=%s,base_url=%s,model_name=%s,api_key_env=%s,enabled=%s,is_default=%s,health_status=%s,last_latency_ms=%s,last_error_code=%s,last_checked_at=%s,updated_at=%s WHERE id=%s AND tenant_id=%s", (provider.name, provider.adapter, provider.base_url, provider.model, provider.api_key_env, provider.enabled, provider.is_default, provider.health_status, provider.last_latency_ms, provider.last_error_code or None, provider.last_checked_at, provider.updated_at.replace(tzinfo=None), provider.id, provider.tenant_id))
            conn.commit()
        return provider

    def get_provider(self, provider_id, tenant_id):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {self._provider_columns} FROM model_providers WHERE id=%s AND tenant_id=%s", (provider_id, tenant_id))
                row = cur.fetchone()
        return self._provider(row) if row else None

    def list_providers(self, tenant_id):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {self._provider_columns} FROM model_providers WHERE tenant_id=%s ORDER BY is_default DESC,name", (tenant_id,))
                rows = cur.fetchall()
        return [self._provider(row) for row in rows]

    def clear_default_provider(self, tenant_id, except_id=""):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE model_providers SET is_default=FALSE WHERE tenant_id=%s AND id<>%s", (tenant_id, except_id))
            conn.commit()

    def create_prompt(self, prompt):
        with connect() as conn:
            with conn.cursor() as cur:
                if prompt.active:
                    cur.execute("UPDATE model_prompt_versions SET active=FALSE WHERE tenant_id=%s AND name=%s", (prompt.tenant_id, prompt.name))
                cur.execute("INSERT INTO model_prompt_versions (id,tenant_id,name,system_prompt,version,owner_id,active,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (prompt.id, prompt.tenant_id, prompt.name, prompt.system_prompt, prompt.version, prompt.owner_id or None, prompt.active, prompt.created_at.replace(tzinfo=None)))
            conn.commit()
        return prompt

    def get_prompt(self, prompt_id, tenant_id):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {self._prompt_columns} FROM model_prompt_versions WHERE id=%s AND tenant_id=%s", (prompt_id, tenant_id))
                row = cur.fetchone()
        return self._prompt(row) if row else None

    def list_prompts(self, tenant_id, name=""):
        sql = f"SELECT {self._prompt_columns} FROM model_prompt_versions WHERE tenant_id=%s"
        params = [tenant_id]
        if name:
            sql += " AND name=%s"
            params.append(name)
        sql += " ORDER BY name,version DESC"
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
        return [self._prompt(row) for row in rows]

    def next_prompt_version(self, tenant_id, name):
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(version),0)+1 FROM model_prompt_versions WHERE tenant_id=%s AND name=%s", (tenant_id, name))
                return int(cur.fetchone()[0])

    def activate_prompt(self, prompt_id, tenant_id):
        target = self.get_prompt(prompt_id, tenant_id)
        if not target:
            return None
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE model_prompt_versions SET active=FALSE WHERE tenant_id=%s AND name=%s", (tenant_id, target.name))
                cur.execute("UPDATE model_prompt_versions SET active=TRUE WHERE id=%s AND tenant_id=%s", (prompt_id, tenant_id))
            conn.commit()
        return self.get_prompt(prompt_id, tenant_id)
