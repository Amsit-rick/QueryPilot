"""Controlled SKILL.md metadata import without code or network execution."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import PurePath
import re
import secrets
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import ValidationError
import yaml
from yaml.nodes import MappingNode

from app.audit import write_audit
from app.config import settings
from app.domain.skill import SkillDefinition
from app.services.prompt_injection_service import inspect_prompt


SKILL_IMPORT_SCHEMA_VERSION = "query-pilot-skill-import/v1"
SKILL_IMPORT_MAX_BYTES = 64 * 1024
SKILL_IMPORT_CONFIRMATION_TTL_SECONDS = 10 * 60

_FRONTMATTER = re.compile(r"\A\ufeff?---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)(.*)\Z", re.S)
_ALLOWED_FIELDS = frozenset({"schema_version", *SkillDefinition.model_fields})
_POLICY_FIELDS = ("allowed_tools", "allowed_roles", "read_only", "priority")
_ANCHOR_OR_TAG = re.compile(r"(?:^|\s)(?:[&*][A-Za-z0-9_-]+|![A-Za-z0-9_!:.-]*)", re.M)


class _DuplicateYamlKey(yaml.YAMLError):
    pass


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_strict_mapping(loader: _StrictSafeLoader, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise _DuplicateYamlKey(f"duplicate mapping key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_strict_mapping,
)


class SkillImportError(ValueError):
    retryable = False

    def __init__(self, code: str, message: str):
        self.code = str(code or "E_SKILL_IMPORT")[:80]
        self.message = str(message or "SKILL.md 导入失败")[:300]
        super().__init__(f"{self.code}:{self.message}")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


class SkillImportService:
    """Parse, preview and confirm a bounded declarative SKILL.md envelope."""

    def __init__(
        self,
        *,
        confirmation_secret: bytes | None = None,
        clock: Callable[[], datetime] | None = None,
        audit_path: str | None = None,
    ):
        configured_secret = settings.jwt_secret.encode("utf-8") if settings.jwt_secret else b""
        self._secret = confirmation_secret or configured_secret or secrets.token_bytes(32)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._audit_path_override = audit_path

    @property
    def audit_path(self) -> str:
        return self._audit_path_override or settings.audit_log_path

    @staticmethod
    def _validate_source(source: dict[str, Any]) -> dict[str, str]:
        allowed = {"kind", "uri", "revision", "publisher"}
        if not isinstance(source, dict) or set(source) != allowed:
            raise SkillImportError("E_SKILL_IMPORT_SOURCE", "导入来源字段必须完整且不能包含未知字段")
        normalized = {key: str(source.get(key) or "").strip() for key in allowed}
        if normalized["kind"] not in {"local", "github", "registry"}:
            raise SkillImportError("E_SKILL_IMPORT_SOURCE", "导入来源类型不受支持")
        if not all(normalized.values()):
            raise SkillImportError("E_SKILL_IMPORT_SOURCE", "导入来源、修订版本和发布者不能为空")
        if len(normalized["uri"]) > 2048 or len(normalized["revision"]) > 128 or len(normalized["publisher"]) > 128:
            raise SkillImportError("E_SKILL_IMPORT_SOURCE", "导入来源字段超过长度限制")
        if normalized["kind"] == "local":
            local_name = normalized["uri"]
            if (
                PurePath(local_name).name != local_name
                or ".." in local_name
                or "/" in local_name
                or "\\" in local_name
                or ":" in local_name
            ):
                raise SkillImportError("E_SKILL_IMPORT_SOURCE", "本地来源只允许文件名，不允许绝对路径")
        else:
            parsed = urlsplit(normalized["uri"])
            if (
                parsed.scheme.lower() != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise SkillImportError("E_SKILL_IMPORT_SOURCE", "远程来源必须是不含凭据、查询参数和片段的 HTTPS URL")
        return normalized

    @staticmethod
    def _validate_signature(signature: dict[str, Any], content_digest: str) -> dict[str, str]:
        allowed = {"algorithm", "digest", "issuer"}
        if not isinstance(signature, dict) or set(signature) != allowed:
            raise SkillImportError("E_SKILL_IMPORT_SIGNATURE", "完整性签名字段必须完整且不能包含未知字段")
        algorithm = str(signature.get("algorithm") or "").strip().lower()
        digest = str(signature.get("digest") or "").strip().lower()
        issuer = str(signature.get("issuer") or "").strip()
        if algorithm != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", digest) or not issuer or len(issuer) > 128:
            raise SkillImportError("E_SKILL_IMPORT_SIGNATURE", "仅支持带签发者的 SHA-256 完整性声明")
        if not hmac.compare_digest(digest, content_digest):
            raise SkillImportError("E_SKILL_IMPORT_DIGEST", "SKILL.md 内容摘要与完整性声明不一致")
        return {"algorithm": algorithm, "digest": digest, "issuer": issuer}

    @staticmethod
    def _parse_definition(content: str) -> tuple[SkillDefinition, bool]:
        raw = str(content or "")
        if len(raw.encode("utf-8")) > SKILL_IMPORT_MAX_BYTES:
            raise SkillImportError("E_SKILL_IMPORT_TOO_LARGE", "SKILL.md 超过 64KB 字节上限")
        match = _FRONTMATTER.fullmatch(raw)
        if match is None:
            raise SkillImportError("E_SKILL_IMPORT_FORMAT", "SKILL.md 必须包含完整 YAML Front Matter")
        frontmatter, body = match.groups()
        if _ANCHOR_OR_TAG.search(frontmatter) or re.search(r"(?m)^\s*<<\s*:", frontmatter):
            raise SkillImportError("E_SKILL_IMPORT_YAML_FEATURE", "不允许 YAML Anchor、Alias、Tag 或 Merge Key")
        try:
            payload = yaml.load(frontmatter, Loader=_StrictSafeLoader)
        except _DuplicateYamlKey as exc:
            raise SkillImportError("E_SKILL_IMPORT_YAML_FEATURE", "不允许 YAML 重复字段") from exc
        except yaml.YAMLError as exc:
            raise SkillImportError("E_SKILL_IMPORT_FORMAT", "SKILL.md Front Matter 不是有效的安全 YAML") from exc
        if not isinstance(payload, dict):
            raise SkillImportError("E_SKILL_IMPORT_SCHEMA", "SKILL.md Front Matter 必须是对象")
        unknown = sorted(str(key) for key in set(payload) - _ALLOWED_FIELDS)
        if unknown:
            raise SkillImportError(
                "E_SKILL_IMPORT_UNSUPPORTED_FIELD",
                "不支持的 SKILL.md 字段：" + "、".join(unknown[:8]),
            )
        if payload.get("schema_version") != SKILL_IMPORT_SCHEMA_VERSION:
            raise SkillImportError("E_SKILL_IMPORT_SCHEMA_VERSION", "SKILL.md schema_version 不受支持")
        definition_payload = {key: value for key, value in payload.items() if key != "schema_version"}
        try:
            definition = SkillDefinition.model_validate(definition_payload)
        except ValidationError as exc:
            fields = sorted({str(item.get("loc", ["definition"])[0]) for item in exc.errors()})
            raise SkillImportError("E_SKILL_IMPORT_SCHEMA", "SKILL.md 声明校验失败：" + "、".join(fields[:8])) from exc
        for field in ("name", "description", "intents", "trigger_terms", "workflow_steps"):
            value = getattr(definition, field)
            texts = value if isinstance(value, tuple) else (value,)
            reasons = sorted({reason for text in texts for reason in inspect_prompt(str(text)).reasons})
            if reasons:
                raise SkillImportError("E_SKILL_IMPORT_UNSAFE_CONTENT", "SKILL.md 包含不允许的指令语义：" + "、".join(reasons[:8]))
        return definition, bool(body.strip())

    @staticmethod
    def _baseline(current_catalog: list[dict[str, Any]], skill_id: str) -> dict[str, Any] | None:
        for item in current_catalog:
            if isinstance(item, dict) and str(item.get("id")) == skill_id:
                return {key: value for key, value in item.items() if key != "deployment"}
        return None

    @staticmethod
    def _diff(definition: SkillDefinition, baseline: dict[str, Any] | None) -> dict[str, Any]:
        current = definition.model_dump(mode="json")
        if baseline is None:
            return {
                "status": "new",
                "base_version": None,
                "changed_fields": sorted(current),
                "changes": [{"field": field, "before": None, "after": current[field]} for field in sorted(current)],
            }
        changed = [field for field in sorted(current) if baseline.get(field) != current.get(field)]
        return {
            "status": "update",
            "base_version": baseline.get("version"),
            "changed_fields": changed,
            "changes": [
                {"field": field, "before": baseline.get(field), "after": current.get(field)}
                for field in changed
            ],
        }

    @staticmethod
    def _enforce_existing_policy(definition: SkillDefinition, baseline: dict[str, Any] | None) -> None:
        if baseline is None:
            return
        current = definition.model_dump(mode="json")
        changed = [field for field in _POLICY_FIELDS if baseline.get(field) != current.get(field)]
        if changed:
            raise SkillImportError(
                "E_SKILL_IMPORT_POLICY",
                "导入不能修改现有 Skill 的平台策略字段：" + "、".join(changed),
            )

    def _build_preview(
        self,
        *,
        content: str,
        source: dict[str, Any],
        signature: dict[str, Any],
        current_catalog: list[dict[str, Any]],
        tenant_id: str,
        actor_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        content_digest = _sha256(content)
        normalized_source = self._validate_source(source)
        normalized_signature = self._validate_signature(signature, content_digest)
        definition, has_body = self._parse_definition(content)
        baseline = self._baseline(current_catalog, definition.id)
        self._enforce_existing_policy(definition, baseline)
        diff = self._diff(definition, baseline)
        source_hash = _sha256(_canonical(normalized_source))
        baseline_hash = _sha256(_canonical(baseline or {}))
        definition_hash = _sha256(_canonical(definition.model_dump(mode="json")))
        binding = {
            "content_digest": content_digest,
            "source_hash": source_hash,
            "baseline_hash": baseline_hash,
            "definition_hash": definition_hash,
            "tenant_hash": _sha256(str(tenant_id)),
            "actor_hash": _sha256(str(actor_id)),
        }
        preview = {
            "format": "query-pilot-skill-import-preview",
            "version": 1,
            "definition": definition.model_dump(mode="json"),
            "source": normalized_source,
            "integrity": {
                **normalized_signature,
                "verified": True,
                "trust_level": "integrity_only",
            },
            "diff": diff,
            "warnings": ["Markdown 正文仅作为人工说明，不会导入或执行"] if has_body else [],
        }
        return preview, binding

    def _issue_token(self, binding: dict[str, Any]) -> tuple[str, str]:
        expires_at = self._clock().astimezone(timezone.utc) + timedelta(seconds=SKILL_IMPORT_CONFIRMATION_TTL_SECONDS)
        payload = {**binding, "exp": int(expires_at.timestamp())}
        encoded = _b64encode(_canonical(payload))
        signature = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", expires_at.isoformat()

    def _verify_token(self, token: str) -> dict[str, Any]:
        try:
            encoded, supplied = str(token or "").split(".", 1)
            expected = _b64encode(hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                raise ValueError("signature")
            payload = json.loads(_b64decode(encoded))
            if not isinstance(payload, dict):
                raise ValueError("payload")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SkillImportError("E_SKILL_IMPORT_CONFIRMATION", "导入确认令牌无效，请重新预览") from exc
        now = int(self._clock().astimezone(timezone.utc).timestamp())
        if int(payload.get("exp") or 0) < now:
            raise SkillImportError("E_SKILL_IMPORT_CONFIRMATION_EXPIRED", "导入确认已过期，请重新预览")
        return payload

    def preview(
        self,
        *,
        content: str,
        source: dict[str, Any],
        signature: dict[str, Any],
        current_catalog: list[dict[str, Any]],
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        preview, binding = self._build_preview(
            content=content,
            source=source,
            signature=signature,
            current_catalog=current_catalog,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        token, expires_at = self._issue_token(binding)
        result = {**preview, "confirmation_token": token, "confirmation_expires_at": expires_at}
        self._audit(
            "skill_import_previewed",
            tenant_id=tenant_id,
            actor_id=actor_id,
            definition=result["definition"],
            content_digest=result["integrity"]["digest"],
            source=result["source"],
        )
        return result

    def confirm(
        self,
        *,
        content: str,
        source: dict[str, Any],
        signature: dict[str, Any],
        confirmation_token: str,
        current_catalog: list[dict[str, Any]],
        tenant_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        token_payload = self._verify_token(confirmation_token)
        preview, binding = self._build_preview(
            content=content,
            source=source,
            signature=signature,
            current_catalog=current_catalog,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        for field in ("content_digest", "source_hash", "definition_hash", "tenant_hash", "actor_hash"):
            if not hmac.compare_digest(str(token_payload.get(field) or ""), str(binding[field])):
                raise SkillImportError("E_SKILL_IMPORT_CONFIRMATION", "导入内容或确认身份已变化，请重新预览")
        if not hmac.compare_digest(str(token_payload.get("baseline_hash") or ""), str(binding["baseline_hash"])):
            raise SkillImportError("E_SKILL_IMPORT_STALE", "当前 Skill 基线已变化，请重新预览差异")
        return {
            **preview,
            "status": "confirmed",
            "confirmed_at": self._clock().astimezone(timezone.utc).isoformat(),
        }

    def record_confirmed(self, *, tenant_id: str, actor_id: str, receipt: dict[str, Any], version_id: str) -> None:
        self._audit(
            "skill_import_confirmed",
            tenant_id=tenant_id,
            actor_id=actor_id,
            definition=receipt.get("definition") or {},
            content_digest=str((receipt.get("integrity") or {}).get("digest") or ""),
            source=receipt.get("source") or {},
            integration_id=version_id,
        )

    def record_rejection(self, *, tenant_id: str, actor_id: str, error_code: str, content: str = "") -> None:
        write_audit(
            self.audit_path,
            trace_id=str(uuid4()),
            tenant_id=tenant_id,
            sql="",
            duration_ms=0,
            event="skill_import_rejected",
            actor_id=actor_id,
            error_code=str(error_code)[:80],
            integration_id=_sha256(str(content))[:24] if content else None,
        )

    def _audit(
        self,
        event: str,
        *,
        tenant_id: str,
        actor_id: str,
        definition: dict[str, Any],
        content_digest: str,
        source: dict[str, Any],
        integration_id: str | None = None,
    ) -> None:
        operation = (
            f"{definition.get('id', 'unknown')}@{definition.get('version', 'unknown')}|"
            f"{source.get('kind', 'unknown')}:{source.get('revision', 'unknown')}|sha256:{content_digest[:12]}"
        )
        write_audit(
            self.audit_path,
            trace_id=str(uuid4()),
            tenant_id=tenant_id,
            sql="",
            duration_ms=0,
            event=event,
            actor_id=actor_id,
            integration_id=integration_id or content_digest[:24],
            operation_name=operation,
        )


__all__ = [
    "SKILL_IMPORT_CONFIRMATION_TTL_SECONDS",
    "SKILL_IMPORT_MAX_BYTES",
    "SKILL_IMPORT_SCHEMA_VERSION",
    "SkillImportError",
    "SkillImportService",
]
