"""Task feedback and sanitized offline evaluation case materialization."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

import sqlglot
from sqlglot import exp

from app.feedback_regression import build_result_contract, build_sql_contract
from app.services.task_service import TaskService


_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|api[_-]?key|access[_-]?token|secret|authorization)\s*[:=]\s*[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{12,}")
_URI_CREDENTIALS = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@")
_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}(?![\w.-])")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")


def contains_credentials(value: str) -> bool:
    return bool(_SECRET_PATTERN.search(value) or _BEARER_PATTERN.search(value) or _URI_CREDENTIALS.search(value))


def sanitize_question(value: str) -> str:
    result = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    result = _BEARER_PATTERN.sub("Bearer [REDACTED]", result)
    result = _URI_CREDENTIALS.sub(lambda match: match.group(0).split("://", 1)[0] + "://[REDACTED]@", result)
    result = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", result)
    result = _PHONE_PATTERN.sub("[REDACTED_PHONE]", result)
    return result.strip()[:2000]


def sanitize_sql_template(sql: str) -> str:
    """Keep query structure while removing literal values from offline cases."""
    try:
        statement = sqlglot.parse_one(sql)
    except sqlglot.errors.ParseError:
        return ""

    def redact(node):
        if isinstance(node, exp.Literal):
            parent = node.parent
            if isinstance(parent, (exp.Limit, exp.Offset)):
                return node
            return exp.Literal.string("[REDACTED]") if node.is_string else exp.Literal.number("0")
        return node

    return statement.transform(redact).sql()


class FeedbackService:
    OUTCOMES = {"accepted", "corrected", "rejected", "failed"}

    def __init__(self, tasks: TaskService):
        self.tasks = tasks

    @staticmethod
    def _source_versions(detail: dict) -> dict:
        understand = next((step.output for step in reversed(detail["steps"]) if step.kind == "understand_question"), {})
        semantics = next((step.output for step in reversed(detail["steps"]) if step.kind == "resolve_semantics"), {})
        reflection = next((step.output for step in reversed(detail["steps"]) if step.kind == "reflection"), {})
        skill = semantics.get("skill") or {}
        return {
            "model": str(understand.get("model") or "unknown")[:128],
            "prompt": str(understand.get("prompt_version") or "unknown")[:128],
            "skill": str(skill.get("version") or "none")[:128],
            "reflection_policy": str(reflection.get("policy_version") or "default")[:128],
        }

    def record(
        self,
        *,
        task_id: str,
        tenant_id: str,
        owner_id: str,
        outcome: str,
        note: str = "",
        corrected_sql: str = "",
        validate_correction: Callable[[str], str] | None = None,
    ) -> dict:
        detail = self.tasks.detail(task_id=task_id, tenant_id=tenant_id, owner_id=owner_id)
        task = detail["task"]
        if outcome not in self.OUTCOMES:
            raise ValueError("E_FEEDBACK_OUTCOME:反馈类型无效")
        if outcome == "failed" and task.status != "failed":
            raise ValueError("E_FEEDBACK_STATE:只有失败任务可以标记为失败案例")
        if outcome != "failed" and task.status != "completed":
            raise ValueError("E_FEEDBACK_STATE:只有已完成任务可以提交该反馈")
        note = note.strip()
        if contains_credentials(note) or contains_credentials(corrected_sql):
            raise ValueError("E_FEEDBACK_SECRET:反馈不得包含密码、密钥或令牌")
        if outcome == "corrected" and not corrected_sql.strip():
            raise ValueError("E_FEEDBACK_CORRECTION:修正反馈必须提供修正 SQL")
        if outcome != "corrected" and corrected_sql.strip():
            raise ValueError("E_FEEDBACK_CORRECTION:只有修正反馈可以提供修正 SQL")

        normalized_correction = ""
        if corrected_sql.strip():
            normalized_correction = validate_correction(corrected_sql.strip()) if validate_correction else corrected_sql.strip()

        latest_sql = next(
            (step.output.get("sql", "") for step in reversed(detail["steps"]) if step.output.get("sql")),
            "",
        )
        expected_sql = normalized_correction or (latest_sql if outcome == "accepted" else "")
        error_code = next(
            (event.payload.get("error_code", "") for event in reversed(detail["events"]) if event.kind in {"task_failed", "task_follow_up_failed"}),
            "",
        )
        feedback_id = str(uuid4())
        recorded_at = datetime.now(timezone.utc).isoformat()
        event = self.tasks.append_event(
            task_id=task_id,
            kind="feedback_recorded",
            payload={
                "feedback_id": feedback_id,
                "outcome": outcome,
                "note": sanitize_question(note),
                "has_correction": bool(normalized_correction),
            },
        )
        safe_question = sanitize_question(task.question)
        expected_sql_contract = build_sql_contract(expected_sql) if expected_sql else {}
        expected_result_contract: dict = {}
        if outcome == "accepted":
            table_artifact = next(
                (artifact for artifact in reversed(detail["artifacts"]) if artifact.kind == "table"),
                None,
            )
            if table_artifact is not None:
                metadata = table_artifact.metadata
                columns = metadata.get("columns")
                rows = metadata.get("rows")
                if isinstance(columns, list) and isinstance(rows, list):
                    complete_preview = (
                        not bool(metadata.get("truncated"))
                        and int(metadata.get("preview_count", len(rows)) or 0) == int(metadata.get("row_count", len(rows)) or 0)
                    )
                    expected_result_contract = build_result_contract(
                        columns,
                        rows,
                        truncated=not complete_preview,
                        order_sensitive=bool(expected_sql_contract.get("order_by_columns")),
                    )
                    if safe_question != task.question:
                        expected_result_contract["verification_eligible"] = False
                        expected_result_contract["ineligible_reason"] = "question_sanitized"
        case = {
            "format": "query-pilot-offline-eval-case-v1",
            "feedback_id": feedback_id,
            "outcome": outcome,
            "question": safe_question,
            "question_sha256": hashlib.sha256(task.question.encode("utf-8")).hexdigest(),
            "question_was_sanitized": safe_question != task.question,
            "datasource_id": task.datasource_id,
            "expected_sql_template": sanitize_sql_template(expected_sql),
            "expected_sql_contract": expected_sql_contract,
            "expected_result_contract": expected_result_contract,
            "source_sql_sha256": hashlib.sha256(latest_sql.encode("utf-8")).hexdigest() if latest_sql else "",
            "error_code": error_code,
            "recorded_at": recorded_at,
            "contains_result_rows": False,
            "review_status": "draft",
            "source_versions": self._source_versions(detail),
        }
        artifact = self.tasks.ensure_artifact(
            task_id=task_id,
            kind="evaluation_case",
            name="离线评测案例",
            idempotency_key=f"{task_id}:evaluation-case:{feedback_id}",
            metadata=case,
        )
        return {"feedback_id": feedback_id, "outcome": outcome, "event_id": event.id, "offline_case": artifact.metadata}

    def review(
        self,
        *,
        task_id: str,
        tenant_id: str,
        feedback_id: str,
        reviewer_id: str,
        decision: str,
        dataset_version: str = "",
        note: str = "",
    ) -> dict:
        detail = self.tasks.detail_for_tenant(task_id=task_id, tenant_id=tenant_id)
        source = next(
            (
                artifact
                for artifact in detail["artifacts"]
                if artifact.kind == "evaluation_case" and artifact.metadata.get("feedback_id") == feedback_id
            ),
            None,
        )
        if source is None:
            raise LookupError("E_FEEDBACK_NOT_FOUND:反馈不存在")
        if any(event.kind == "feedback_reviewed" and event.payload.get("feedback_id") == feedback_id for event in detail["events"]):
            raise ValueError("E_FEEDBACK_ALREADY_REVIEWED:反馈已经审核")
        if contains_credentials(note):
            raise ValueError("E_FEEDBACK_SECRET:审核备注不得包含密码、密钥或令牌")
        reviewed_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "feedback_id": feedback_id,
            "review_status": decision,
            "reviewer_id": reviewer_id,
            "dataset_version": dataset_version.strip(),
            "note": sanitize_question(note),
            "reviewed_at": reviewed_at,
        }
        self.tasks.append_event(task_id=task_id, kind="feedback_reviewed", payload=payload)
        if decision == "approved":
            approved_case = {
                **{key: value for key, value in source.metadata.items() if key != "idempotency_key"},
                **payload,
                "format": "query-pilot-approved-eval-case-v1",
                "contains_result_rows": False,
            }
            artifact = self.tasks.ensure_artifact(
                task_id=task_id,
                kind="approved_evaluation_case",
                name="已批准回归案例",
                idempotency_key=f"{task_id}:approved-evaluation-case:{feedback_id}:{dataset_version}",
                metadata=approved_case,
            )
            return artifact.metadata
        return payload

    def approved_dataset(self, *, tenant_id: str, dataset_version: str) -> dict:
        artifacts = self.tasks.list_artifacts_for_tenant(
            tenant_id=tenant_id,
            kind="approved_evaluation_case",
            limit=10000,
        )
        cases = [
            {key: value for key, value in artifact.metadata.items() if key != "idempotency_key"}
            for artifact in artifacts
            if artifact.metadata.get("dataset_version") == dataset_version
        ]
        return {
            "format": "query-pilot-feedback-eval-dataset",
            "version": 1,
            "dataset_version": dataset_version,
            "case_count": len(cases),
            "cases": cases,
            "limitations": [
                "Cases are administrator-approved user feedback, not automatically trusted ground truth.",
                "Prompt, Skill and model changes require an explicit release workflow; this dataset never mutates them.",
            ],
        }


__all__ = ["FeedbackService", "contains_credentials", "sanitize_question", "sanitize_sql_template"]
