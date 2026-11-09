"""Deterministic Skill routing plus tenant-scoped release governance."""

from __future__ import annotations

import hashlib
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.audit import write_audit
from app.config import settings
from app.domain.skill import SkillDefinition, SkillVersion
from app.repositories.memory_skill_repository import InMemorySkillRepository


BUILTIN_SKILLS: tuple[SkillDefinition, ...] = (
    SkillDefinition(
        id="schema-exploration",
        version="1.0.0",
        name="Schema 探查",
        description="在当前授权范围内识别表、字段和关系，不读取未授权元数据。",
        intents=("schema_exploration", "metadata_discovery"),
        trigger_terms=("schema", "表结构", "有哪些表", "字段", "主键", "外键", "表关系"),
        allowed_tools=("schema.list",),
        workflow_steps=("读取授权 Schema", "识别候选表字段", "返回关系和限制说明"),
        priority=80,
    ),
    SkillDefinition(
        id="sales-analysis",
        version="1.0.0",
        name="销售分析",
        description="围绕销售额、订单、客户和区域维度规划只读分析。",
        intents=("sales_query", "sales_breakdown"),
        trigger_terms=("销售", "销售额", "营收", "成交", "订单金额", "客单价"),
        allowed_tools=("schema.list", "query.plan", "result.get"),
        workflow_steps=("确认销售指标口径", "选择维度和过滤条件", "生成受控查询计划", "解释结果限制"),
        priority=70,
    ),
    SkillDefinition(
        id="receivables-analysis",
        version="1.0.0",
        name="回款分析",
        description="分析待回款、应收账款和付款状态，显式确认时间与状态口径。",
        intents=("receivables_query", "payment_status_analysis"),
        trigger_terms=("待回款", "回款", "应收", "欠款", "付款状态", "逾期"),
        allowed_tools=("schema.list", "query.plan", "result.get"),
        workflow_steps=("确认回款状态口径", "定位订单与支付关系", "生成受控查询计划", "输出口径说明"),
        priority=90,
    ),
    SkillDefinition(
        id="trend-analysis",
        version="1.0.0",
        name="趋势与周期分析",
        description="对时间序列执行趋势、环比、同比和候选异常分析。",
        intents=("trend", "period_comparison", "anomaly_detection"),
        trigger_terms=("趋势", "同比", "环比", "增长", "下降", "变化", "异常", "波动"),
        allowed_tools=("schema.list", "query.plan", "result.get"),
        workflow_steps=("确认时间粒度", "选择指标与基期", "生成受控查询计划", "执行确定性趋势解释"),
        priority=100,
    ),
    SkillDefinition(
        id="sql-diagnostics",
        version="1.0.0",
        name="SQL 诊断",
        description="诊断只读 SQL 的语法、字段、关系和潜在成本问题，不执行写操作。",
        intents=("sql_diagnostics", "query_optimization"),
        trigger_terms=("sql", "语法错误", "执行慢", "慢查询", "优化", "explain", "笛卡尔积", "报错"),
        allowed_tools=("schema.list", "query.plan"),
        workflow_steps=("解析问题 SQL", "核对授权 Schema", "执行静态安全校验", "给出受限修正建议"),
        priority=95,
    ),
)


class SkillService:
    def __init__(
        self,
        skills: tuple[SkillDefinition, ...] = BUILTIN_SKILLS,
        *,
        repository=None,
    ):
        ids = [skill.id for skill in skills]
        if len(ids) != len(set(ids)):
            raise ValueError("skill ids must be unique")
        self._skills = tuple(skills)
        self._builtin_by_id = {skill.id: skill for skill in skills}
        self.repository = repository or InMemorySkillRepository()

    @staticmethod
    def _identity(identity: dict[str, Any] | None) -> tuple[str, str, set[str] | None]:
        if identity is None:
            return "default", "anonymous", None
        return (
            str(identity.get("tenant_id", "default")),
            str(identity.get("subject", "anonymous")),
            set(identity.get("roles", [])),
        )

    @staticmethod
    def _bucket(tenant_id: str, subject: str, skill_id: str, version: str) -> int:
        value = f"{tenant_id}|{subject}|{skill_id}|{version}".encode("utf-8")
        return int.from_bytes(hashlib.sha256(value).digest()[:8], "big") % 100

    def _resolve(
        self, tenant_id: str, subject: str, skill_id: str
    ) -> tuple[SkillDefinition | None, str, SkillVersion | None, int]:
        versions = self.repository.list(tenant_id, skill_id)
        active = next((item for item in versions if item.state == "active"), None)
        canary = next((item for item in versions if item.state == "canary"), None)
        bucket = (
            self._bucket(
                tenant_id,
                subject,
                skill_id,
                canary.definition.version,
            )
            if canary
            else 0
        )
        if canary and bucket < canary.canary_percent:
            return canary.definition, "canary", canary, bucket
        if active:
            return active.definition, "active", active, bucket
        builtin = self._builtin_by_id.get(skill_id)
        return builtin, "builtin" if builtin else "unavailable", None, bucket

    @staticmethod
    def _public_skill(
        definition: SkillDefinition, state: str, version_id: str | None
    ) -> dict:
        payload = definition.public_dict()
        payload["deployment"] = {"state": state, "version_id": version_id}
        return payload

    def list_skills(self, identity: dict[str, Any] | None = None) -> list[dict]:
        tenant_id, subject, roles = self._identity(identity)
        stored_ids = {
            item.definition.id for item in self.repository.list(tenant_id)
        }
        skill_ids = sorted(set(self._builtin_by_id) | stored_ids)
        result = []
        for skill_id in skill_ids:
            definition, state, record, _ = self._resolve(tenant_id, subject, skill_id)
            if definition is None:
                continue
            if roles is not None and not roles.intersection(definition.allowed_roles):
                continue
            result.append(self._public_skill(definition, state, record.id if record else None))
        return result

    def list_versions(self, tenant_id: str, skill_id: str = "") -> list[SkillVersion]:
        return self.repository.list(tenant_id, skill_id)

    def list_admin_catalog(self, identity: dict[str, Any]) -> list[dict]:
        tenant_id, subject, _ = self._identity(identity)
        stored_ids = {
            item.definition.id for item in self.repository.list(tenant_id)
        }
        catalog = []
        for skill_id in sorted(set(self._builtin_by_id) | stored_ids):
            definition, state, record, _ = self._resolve(tenant_id, subject, skill_id)
            if definition is None:
                continue
            payload = definition.model_dump(mode="json")
            payload["deployment"] = {"state": state, "version_id": record.id if record else None}
            catalog.append(payload)
        return catalog

    def create_version(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        definition: SkillDefinition,
    ) -> SkillVersion:
        existing = self.repository.list(tenant_id, definition.id)
        if any(item.definition.version == definition.version for item in existing):
            raise ValueError("E_SKILL_VERSION_DUPLICATE:Skill 版本已存在")
        builtin = self._builtin_by_id.get(definition.id)
        if builtin and definition.version == builtin.version:
            raise ValueError("E_SKILL_VERSION_RESERVED:内置版本号不可覆盖")
        record = self.repository.create(
            SkillVersion(
                tenant_id=tenant_id,
                definition=definition,
                state="draft",
                created_by=actor_id,
            )
        )
        self._audit_release("skill_version_created", record, actor_id)
        return record

    def start_canary(
        self,
        version_id: str,
        *,
        tenant_id: str,
        actor_id: str,
        percentage: int,
    ) -> SkillVersion:
        if percentage < 1 or percentage > 100:
            raise ValueError("E_SKILL_CANARY_PERCENT:灰度比例必须为 1 到 100")
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("Skill 版本不存在")
        if current.state not in {"draft", "canary"}:
            raise ValueError("E_SKILL_CANARY_STATE:仅草稿或灰度版本可调整灰度")
        updated = self.repository.set_canary(version_id, tenant_id, percentage)
        if updated is None:
            raise LookupError("Skill 版本不存在")
        self._audit_release("skill_version_canary", updated, actor_id)
        return updated

    def publish_version(
        self, version_id: str, *, tenant_id: str, actor_id: str
    ) -> SkillVersion:
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("Skill 版本不存在")
        if current.state not in {"draft", "canary"}:
            raise ValueError("E_SKILL_PUBLISH_STATE:仅草稿或灰度版本可发布")
        versions = self.repository.list(tenant_id, current.definition.id)
        builtin = self._builtin_by_id.get(current.definition.id)
        if builtin and not any(
            item.definition.version == builtin.version for item in versions
        ):
            self.repository.create(
                SkillVersion(
                    tenant_id=tenant_id,
                    definition=builtin,
                    state="retired",
                    created_by="system:builtin",
                )
            )
        updated = self.repository.activate(version_id, tenant_id)
        if updated is None:
            raise LookupError("Skill 版本不存在")
        self._audit_release("skill_version_published", updated, actor_id)
        return updated

    def rollback_version(
        self, version_id: str, *, tenant_id: str, actor_id: str
    ) -> SkillVersion:
        current = self.repository.get(version_id, tenant_id)
        if current is None:
            raise LookupError("Skill 版本不存在")
        if current.state != "retired":
            raise ValueError("E_SKILL_ROLLBACK_STATE:仅历史版本可回滚")
        updated = self.repository.activate(version_id, tenant_id)
        if updated is None:
            raise LookupError("Skill 版本不存在")
        self._audit_release("skill_version_rolled_back", updated, actor_id)
        return updated

    def preview_resolution(
        self, *, tenant_id: str, subject: str, skill_id: str
    ) -> dict[str, Any]:
        versions = self.repository.list(tenant_id, skill_id)
        active = next((item for item in versions if item.state == "active"), None)
        canary = next((item for item in versions if item.state == "canary"), None)
        definition, state, _, bucket = self._resolve(tenant_id, subject, skill_id)
        return {
            "skill_id": skill_id,
            "subject": subject,
            "bucket": bucket,
            "canary_percent": canary.canary_percent if canary else 0,
            "canary_version": canary.definition.version if canary else None,
            "active_version": (
                active.definition.version
                if active
                else self._builtin_by_id.get(skill_id).version
                if self._builtin_by_id.get(skill_id)
                else None
            ),
            "selected_version": definition.version if definition else None,
            "selected_state": state,
        }

    @staticmethod
    def _audit_release(event: str, record: SkillVersion, actor_id: str) -> None:
        write_audit(
            settings.audit_log_path,
            trace_id=str(uuid4()),
            tenant_id=record.tenant_id,
            sql="",
            duration_ms=0,
            event=event,
            actor_id=actor_id,
            integration_id=record.id,
            operation_name=f"{record.definition.id}@{record.definition.version}",
        )

    def route(
        self,
        question: str,
        *,
        identity: dict[str, Any] | None = None,
        datasource_id: str | None = None,
        audit: bool = True,
    ) -> dict[str, Any]:
        started = perf_counter()
        normalized = question.strip().casefold()
        if not normalized or len(question) > 2000:
            raise ValueError("question must contain 1 to 2000 characters")
        tenant_id, subject, roles = self._identity(identity)
        ranked: list[tuple[int, SkillDefinition, tuple[str, ...]]] = []
        effective = self.list_skills(identity)
        for payload in effective:
            skill_id = payload["id"]
            skill, _, _, _ = self._resolve(tenant_id, subject, skill_id)
            if skill is None:
                continue
            if roles is not None and not roles.intersection(skill.allowed_roles):
                continue
            matched = tuple(term for term in skill.trigger_terms if term.casefold() in normalized)
            if not matched:
                continue
            score = len(matched) * 100 + skill.priority
            ranked.append((score, skill, matched))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        selected = ranked[0] if ranked else None
        selected_payload = None
        if selected:
            definition, state, record, _ = self._resolve(tenant_id, subject, selected[1].id)
            selected_payload = self._public_skill(
                definition,
                state,
                record.id if record else None,
            )
        result = {
            "version": 1,
            "read_only": True,
            "datasource_id": datasource_id,
            "selected_skill": selected_payload,
            "requires_clarification": selected is None,
            "evidence": {
                "matched_terms": list(selected[2]) if selected else [],
                "score": selected[0] if selected else 0,
                "method": "deterministic_keyword_priority_v1",
            },
            "candidates": [
                {
                    "skill_id": skill.id,
                    "version": skill.version,
                    "score": score,
                    "matched_terms": list(matched),
                }
                for score, skill, matched in ranked
            ],
        }
        if identity is not None and audit:
            write_audit(
                settings.audit_log_path,
                trace_id=str(uuid4()),
                tenant_id=identity.get("tenant_id", "default"),
                sql="",
                duration_ms=int((perf_counter() - started) * 1000),
                event="skill_routed",
                actor_id=str(identity.get("subject", "")),
            )
        return result


def get_skill_service() -> SkillService:
    # Kept for compatibility with existing imports; runtime owns persistence choice.
    from app.skill_runtime import get_skill_service as get_runtime_skill_service

    return get_runtime_skill_service()


__all__ = ["BUILTIN_SKILLS", "SkillService", "get_skill_service"]
