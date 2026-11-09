import hashlib
import json
import os
import sys
from math import isfinite
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .metrics_runtime import record_audit_event, record_audit_write_failure


def _safe_nonnegative(value, *, integer: bool = False) -> tuple[int | float, bool]:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return (0 if integer else 0.0), False
    if not isfinite(numeric) or numeric < 0:
        return (0 if integer else 0.0), False
    return (int(numeric) if integer else numeric), True


def rotate_audit_log(path: Path, incoming_bytes: int, *, max_bytes: int, backup_count: int) -> None:
    """Rotate before appending so the active log stays within its byte budget."""
    if not path.exists() or max_bytes < 1 or backup_count < 1:
        return
    if path.stat().st_size + incoming_bytes <= max_bytes:
        return
    oldest = path.with_name(f"{path.name}.{backup_count}")
    try:
        if oldest.exists():
            oldest.unlink()
        for index in range(backup_count - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        # Preserve the audit event if rotation cannot complete; centralized
        # log collection should still capture the over-budget file.
        return


def write_audit(path: str, *, trace_id: str, sql: str, duration_ms: int, row_count: int = 0, truncated: bool = False, large_result: bool = False, error_code: str | None = None, model: str | None = None, prompt_version: str = "v1", dataset_version: str | None = None, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0, usage_source: str = "none", event: str = "query", actor_id: str | None = None, approval_id: str | None = None, task_id: str | None = None, tenant_id: str | None = None, cost_backend: str | None = None, cost_supported: bool | None = None, estimated_rows: int | None = None, estimated_cost: float | None = None, full_scan: bool | None = None, index_used: bool | None = None, integration_id: str | None = None, operation_name: str | None = None, reflection_failure_type: str | None = None, reflection_action: str | None = None, reflection_attempt: int | None = None, reflection_reason_codes: list[str] | tuple[str, ...] | None = None, reflection_final_status: str | None = None, tool_replay_class: str | None = None, tool_replay_policy_version: str | None = None) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_duration, _ = _safe_nonnegative(duration_ms, integer=True)
    safe_rows, _ = _safe_nonnegative(row_count, integer=True)
    safe_input, _ = _safe_nonnegative(input_tokens, integer=True)
    safe_output, _ = _safe_nonnegative(output_tokens, integer=True)
    safe_cost, _ = _safe_nonnegative(cost_usd)
    safe_estimated_rows, _ = _safe_nonnegative(estimated_rows, integer=True) if estimated_rows is not None else (None, True)
    safe_estimated_cost, _ = _safe_nonnegative(estimated_cost) if estimated_cost is not None else (None, True)
    safe_reflection_attempt, _ = _safe_nonnegative(reflection_attempt, integer=True) if reflection_attempt is not None else (None, True)
    record = {"schema_version": 1, "log_type": "query_pilot_audit", "timestamp": datetime.now(timezone.utc).isoformat(), "trace_id": str(trace_id)[:256], "task_id": str(task_id)[:256] if task_id is not None else None, "tenant_id": str(tenant_id)[:256] if tenant_id is not None else None, "event": event, "actor_id": actor_id, "approval_id": approval_id, "integration_id": str(integration_id)[:256] if integration_id is not None else None, "operation_name": str(operation_name)[:128] if operation_name is not None else None, "sql_sha256": hashlib.sha256(str(sql).encode()).hexdigest(), "duration_ms": safe_duration, "row_count": safe_rows, "truncated": bool(truncated), "large_result": bool(large_result), "error_code": error_code, "model": model, "prompt_version": prompt_version, "dataset_version": dataset_version, "input_tokens": safe_input, "output_tokens": safe_output, "cost_usd": safe_cost, "usage_source": usage_source if usage_source in {"provider", "estimate", "none"} else "none", "cost_backend": str(cost_backend)[:64] if cost_backend else None, "cost_supported": cost_supported if cost_supported is None else bool(cost_supported), "estimated_rows": safe_estimated_rows, "estimated_cost": safe_estimated_cost, "full_scan": full_scan if full_scan is None else bool(full_scan), "index_used": index_used if index_used is None else bool(index_used), "reflection_failure_type": str(reflection_failure_type)[:64] if reflection_failure_type else None, "reflection_action": str(reflection_action)[:64] if reflection_action else None, "reflection_attempt": safe_reflection_attempt, "reflection_reason_codes": [str(item)[:80] for item in (reflection_reason_codes or [])[:8]], "reflection_final_status": str(reflection_final_status)[:64] if reflection_final_status else None, "tool_replay_class": tool_replay_class if tool_replay_class in {"safe", "unsafe", "compensatable"} else None, "tool_replay_policy_version": str(tool_replay_policy_version)[:64] if tool_replay_policy_version else None}
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    rotate_audit_log(
        target,
        len(payload),
        max_bytes=settings.audit_log_max_bytes,
        backup_count=settings.audit_log_backup_count,
    )
    try:
        with target.open("ab") as handle:
            handle.write(payload)
    except OSError:
        record_audit_write_failure()
        raise
    record_audit_event(record)
    if settings.audit_stdout_enabled:
        try:
            sys.stdout.write(payload.decode("utf-8"))
            sys.stdout.flush()
        except (OSError, ValueError):
            # The local append-only file remains authoritative if the process
            # output stream is unavailable.
            pass


def _audit_paths(path: Path, backup_count: int) -> tuple[Path, ...]:
    return (path, *(path.with_name(f"{path.name}.{index}") for index in range(1, backup_count + 1)))


def _record_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summarize(
    path: str,
    *,
    backup_count: int | None = None,
    since: datetime | None = None,
) -> dict:
    target = Path(path)
    records = []
    invalid_records = 0
    invalid_values = 0
    if since is not None:
        since = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
    configured_backups = settings.audit_log_backup_count if backup_count is None else backup_count
    configured_backups = max(0, min(int(configured_backups), 100))
    for source in _audit_paths(target, configured_backups):
        if source.exists():
            with source.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError("audit record must be an object")
                        record_time = _record_timestamp(value.get("timestamp"))
                        if since is not None and record_time is not None and record_time < since:
                            continue
                        normalized = dict(value)
                        for field, integer in (("duration_ms", False), ("row_count", True), ("cost_usd", False), ("input_tokens", True), ("output_tokens", True)):
                            normalized[field], valid = _safe_nonnegative(value.get(field, 0), integer=integer)
                            invalid_values += int(not valid)
                        records.append(normalized)
                    except (json.JSONDecodeError, ValueError):
                        invalid_records += 1
    repair_actions = {"regenerate_sql", "retrieve_more_schema", "retry_execution"}
    reflection_records = [record for record in records if record.get("event") == "reflection"]
    repair_records = [record for record in reflection_records if record.get("reflection_action") in repair_actions]
    reason_codes = [
        str(code)
        for record in records
        for code in (record.get("reflection_reason_codes") or [])
    ]
    error_codes = [str(record.get("error_code") or "") for record in records]
    return {
        "requests": len(records),
        "invalid_records": invalid_records,
        "invalid_values": invalid_values,
        "errors": sum(bool(r.get("error_code")) for r in records),
        "total_rows_returned": sum(r["row_count"] for r in records),
        "truncated_queries": sum(bool(r.get("truncated")) for r in records),
        "large_result_queries": sum(bool(r.get("large_result")) for r in records),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 8),
        "total_input_tokens": sum(r["input_tokens"] for r in records),
        "total_output_tokens": sum(r["output_tokens"] for r in records),
        "avg_duration_ms": round(sum(r["duration_ms"] for r in records) / len(records), 2) if records else 0,
        "reflection_decisions": len(reflection_records),
        "reflection_repair_successes": sum(record.get("reflection_final_status") == "completed" for record in repair_records),
        "reflection_repair_failures": sum(record.get("reflection_final_status") not in {None, "completed"} for record in repair_records),
        "reflection_invalid_retries": sum("BUDGET_EXHAUSTED" in code for code in reason_codes),
        "reflection_rollbacks": sum(record.get("event") == "reflection_rollout_rolled_back" for record in records),
        "checkpoint_recovery_failures": sum(code.startswith(("E_RECOVERY_", "E_CHECKPOINT_")) for code in error_codes),
        "budget_exhausted": sum("BUDGET_EXHAUSTED" in code for code in [*reason_codes, *error_codes]),
        "critic_timeouts": sum(code == "SEMANTIC_CRITIC_TIMEOUT" for code in reason_codes),
        "critic_invalid_responses": sum(code == "SEMANTIC_CRITIC_INVALID" for code in reason_codes),
    }
