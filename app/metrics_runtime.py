"""Process-local Prometheus metrics updated at write time.

JSONL audit and trace files remain replay evidence. They are deliberately not
read on the `/metrics` request path.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, multiprocess


REQUEST_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
AGENT_STAGE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)


class RuntimeMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter("query_pilot_requests_total", "Audit events recorded by Query Pilot", registry=self.registry)
        self.errors = Counter("query_pilot_errors_total", "Audit events with an error code", registry=self.registry)
        self.audit_write_failures = Counter("query_pilot_audit_write_failures_total", "Audit records that could not be persisted", registry=self.registry)
        self.request_duration = Histogram(
            "query_pilot_request_duration_seconds",
            "Recorded operation duration in seconds",
            buckets=REQUEST_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.cost = Counter("query_pilot_cost_usd_total", "Recorded model cost in USD", registry=self.registry)
        self.input_tokens = Counter("query_pilot_input_tokens_total", "Recorded model input tokens", registry=self.registry)
        self.output_tokens = Counter("query_pilot_output_tokens_total", "Recorded model output tokens", registry=self.registry)
        self.rows = Counter("query_pilot_rows_returned_total", "Rows returned by recorded query events", registry=self.registry)
        self.truncated = Counter("query_pilot_truncated_queries_total", "Recorded truncated query results", registry=self.registry)
        self.large_results = Counter("query_pilot_large_result_queries_total", "Recorded large query results", registry=self.registry)
        self.reflection_decisions = Counter("query_pilot_reflection_decisions_total", "Reflection decisions", registry=self.registry)
        self.reflection_repair_success = Counter("query_pilot_reflection_repair_success_total", "Successful reflection repairs", registry=self.registry)
        self.reflection_repair_failure = Counter("query_pilot_reflection_repair_failure_total", "Failed reflection repairs", registry=self.registry)
        self.reflection_invalid_retry = Counter("query_pilot_reflection_invalid_retry_total", "Reflection retries rejected by budget", registry=self.registry)
        self.reflection_rollbacks = Counter("query_pilot_reflection_rollbacks_total", "Reflection rollout rollbacks", registry=self.registry)
        self.checkpoint_recovery_failures = Counter("query_pilot_checkpoint_recovery_failures_total", "Checkpoint or recovery failures", registry=self.registry)
        self.budget_exhausted = Counter("query_pilot_budget_exhausted_total", "Agent or model budget exhaustion events", registry=self.registry)
        self.critic_timeouts = Counter("query_pilot_critic_timeouts_total", "Semantic critic timeouts", registry=self.registry)
        self.critic_invalid = Counter("query_pilot_critic_invalid_responses_total", "Invalid semantic critic responses", registry=self.registry)
        self.agent_spans = Counter(
            "query_pilot_agent_spans_total",
            "Agent stage spans",
            labelnames=("stage", "status"),
            registry=self.registry,
        )
        self.agent_stage_duration = Histogram(
            "query_pilot_agent_stage_duration_seconds",
            "Agent stage duration in seconds",
            labelnames=("stage", "status"),
            buckets=AGENT_STAGE_BUCKETS,
            registry=self.registry,
        )
        self.trace_export_failures = Counter(
            "query_pilot_trace_export_failures_total",
            "Trace exporter failures",
            registry=self.registry,
        )
        self.schema_embedding_requests = Counter(
            "query_pilot_schema_embedding_requests_total",
            "Schema embedding scorer requests",
            labelnames=("status",),
            registry=self.registry,
        )
        self.schema_embedding_duration = Histogram(
            "query_pilot_schema_embedding_duration_seconds",
            "Schema embedding scorer duration in seconds",
            labelnames=("status",),
            buckets=AGENT_STAGE_BUCKETS,
            registry=self.registry,
        )
        self.schema_embedding_documents = Counter(
            "query_pilot_schema_embedding_documents_total",
            "Schema documents considered by the embedding scorer",
            registry=self.registry,
        )
        self.schema_embedding_cache_hits = Counter(
            "query_pilot_schema_embedding_cache_hits_total",
            "Schema embedding document cache hits",
            registry=self.registry,
        )
        self.schema_embedding_cache_misses = Counter(
            "query_pilot_schema_embedding_cache_misses_total",
            "Schema embedding document cache misses",
            registry=self.registry,
        )

    def record_audit(self, record: dict[str, Any]) -> None:
        self.requests.inc()
        if record.get("error_code"):
            self.errors.inc()
        self.request_duration.observe(max(0.0, float(record.get("duration_ms", 0) or 0)) / 1000.0)
        self.cost.inc(max(0.0, float(record.get("cost_usd", 0) or 0)))
        self.input_tokens.inc(max(0, int(record.get("input_tokens", 0) or 0)))
        self.output_tokens.inc(max(0, int(record.get("output_tokens", 0) or 0)))
        self.rows.inc(max(0, int(record.get("row_count", 0) or 0)))
        if record.get("truncated"):
            self.truncated.inc()
        if record.get("large_result"):
            self.large_results.inc()

        event = str(record.get("event") or "")
        action = str(record.get("reflection_action") or "")
        final_status = record.get("reflection_final_status")
        reason_codes = [str(code) for code in (record.get("reflection_reason_codes") or [])]
        error_code = str(record.get("error_code") or "")
        if event == "reflection":
            self.reflection_decisions.inc()
            if action in {"regenerate_sql", "retrieve_more_schema", "retry_execution"}:
                if final_status == "completed":
                    self.reflection_repair_success.inc()
                elif final_status is not None:
                    self.reflection_repair_failure.inc()
        if any("BUDGET_EXHAUSTED" in code for code in reason_codes):
            self.reflection_invalid_retry.inc()
        if event == "reflection_rollout_rolled_back":
            self.reflection_rollbacks.inc()
        if error_code.startswith(("E_RECOVERY_", "E_CHECKPOINT_")):
            self.checkpoint_recovery_failures.inc()
        exhausted_count = sum("BUDGET_EXHAUSTED" in code for code in [*reason_codes, error_code])
        if exhausted_count:
            self.budget_exhausted.inc(exhausted_count)
        timeout_count = sum(code == "SEMANTIC_CRITIC_TIMEOUT" for code in reason_codes)
        if timeout_count:
            self.critic_timeouts.inc(timeout_count)
        invalid_count = sum(code == "SEMANTIC_CRITIC_INVALID" for code in reason_codes)
        if invalid_count:
            self.critic_invalid.inc(invalid_count)

    def record_trace(self, record: dict[str, Any]) -> None:
        stage = str(record.get("stage") or "unknown")[:64]
        status = "error" if record.get("status") == "error" else "ok"
        duration_seconds = max(0.0, float(record.get("duration_ms", 0) or 0)) / 1000.0
        self.agent_spans.labels(stage=stage, status=status).inc()
        self.agent_stage_duration.labels(stage=stage, status=status).observe(duration_seconds)


_lock = threading.Lock()
_metrics = RuntimeMetrics()


def record_audit_event(record: dict[str, Any]) -> None:
    try:
        _metrics.record_audit(record)
    except Exception:
        return


def record_audit_write_failure() -> None:
    try:
        _metrics.audit_write_failures.inc()
    except Exception:
        return


def record_agent_trace(record: dict[str, Any]) -> None:
    try:
        _metrics.record_trace(record)
    except Exception:
        return


def record_trace_export_failure() -> None:
    try:
        _metrics.trace_export_failures.inc()
    except Exception:
        return


def record_schema_embedding_request(
    *,
    status: str,
    duration_seconds: float,
    documents: int,
    cache_hits: int,
    cache_misses: int,
) -> None:
    try:
        normalized_status = "ok" if status == "ok" else "error"
        _metrics.schema_embedding_requests.labels(status=normalized_status).inc()
        _metrics.schema_embedding_duration.labels(status=normalized_status).observe(max(0.0, float(duration_seconds)))
        _metrics.schema_embedding_documents.inc(max(0, int(documents)))
        _metrics.schema_embedding_cache_hits.inc(max(0, int(cache_hits)))
        _metrics.schema_embedding_cache_misses.inc(max(0, int(cache_misses)))
    except Exception:
        return


def _multiprocess_dir() -> str:
    return os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()


def render_metrics() -> bytes:
    multiprocess_dir = _multiprocess_dir()
    if multiprocess_dir:
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry, path=multiprocess_dir)
        return generate_latest(registry)
    return generate_latest(_metrics.registry)


def metrics_runtime_status() -> dict[str, object]:
    multiprocess_dir = _multiprocess_dir()
    return {
        "mode": "multiprocess" if multiprocess_dir else "process",
        "multiprocess_directory_configured": bool(multiprocess_dir),
    }


def shutdown_metrics_runtime() -> None:
    multiprocess_dir = _multiprocess_dir()
    if not multiprocess_dir:
        return
    try:
        multiprocess.mark_process_dead(os.getpid(), path=multiprocess_dir)
    except Exception:
        return


def reset_metrics_for_tests() -> None:
    global _metrics
    with _lock:
        _metrics = RuntimeMetrics()


__all__ = [
    "record_agent_trace",
    "record_audit_event",
    "record_audit_write_failure",
    "record_schema_embedding_request",
    "record_trace_export_failure",
    "metrics_runtime_status",
    "render_metrics",
    "reset_metrics_for_tests",
    "shutdown_metrics_runtime",
]
