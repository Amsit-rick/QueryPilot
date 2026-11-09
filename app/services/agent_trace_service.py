"""Safe Agent span tracing without prompts, parameters or result payloads."""

from __future__ import annotations

import inspect
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable


TRACE_SCHEMA_VERSION = 1
ALLOWED_TRACE_STAGES = frozenset(
    {
        "request",
        "schema_retrieve",
        "skill_resolve",
        "plan",
        "sql_generate",
        "validate",
        "explain",
        "execute",
        "verify",
        "reflect",
        "analyze",
        "materialize",
        "postprocess",
    }
)


class InMemoryTraceExporter:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def emit(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.records.append(dict(record))


class JsonlTraceExporter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def emit(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock, self.path.open("ab") as handle:
            handle.write(payload)


class ResilientMultiTraceExporter:
    """Fan out safe records while never letting telemetry break a query."""

    def __init__(self, exporters: list[Any]):
        self.exporters = list(exporters)
        self.export_failures = 0
        self._lock = threading.Lock()

    def emit(self, record: dict[str, Any]) -> None:
        for exporter in self.exporters:
            try:
                exporter.emit(record)
            except Exception:
                from app.metrics_runtime import record_trace_export_failure

                record_trace_export_failure()
                with self._lock:
                    self.export_failures += 1


class OtlpTraceExporter:
    """Optional OTLP exporter. Only safe, allowlisted span attributes are emitted."""

    def __init__(self, *, endpoint: str, service_name: str, headers: str = ""):
        if not endpoint.strip():
            raise ValueError("OTLP endpoint is required")
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.trace import Status, StatusCode

        parsed_headers = {}
        for item in headers.split(","):
            key, separator, value = item.partition("=")
            if separator and key.strip():
                parsed_headers[key.strip()] = value.strip()
        provider = TracerProvider(resource=Resource.create({"service.name": service_name or "query-pilot"}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.strip(), headers=parsed_headers or None))
        )
        self._tracer = provider.get_tracer("query-pilot.agent")
        self._status_type = Status
        self._status_error = StatusCode.ERROR

    def emit(self, record: dict[str, Any]) -> None:
        attributes = {
            "query_pilot.trace_id": record["trace_id"],
            "query_pilot.task_id": record["task_id"],
            "query_pilot.tenant_id": record["tenant_id"],
            "query_pilot.stage": record["stage"],
            "query_pilot.status": record["status"],
            "query_pilot.duration_ms": record["duration_ms"],
        }
        if record.get("error_code"):
            attributes["error.type"] = record["error_code"]
        with self._tracer.start_as_current_span(
            f"query_pilot.agent.{record['stage']}",
            attributes=attributes,
        ) as span:
            if record["status"] == "error":
                span.set_status(self._status_type(self._status_error))


def build_trace_exporter(
    *,
    mode: str,
    jsonl_path: str | Path,
    otlp_endpoint: str = "",
    otlp_headers: str = "",
    service_name: str = "query-pilot",
):
    local = JsonlTraceExporter(jsonl_path)
    if str(mode).lower() != "otlp":
        return local
    exporters: list[Any] = [local]
    try:
        exporters.append(
            OtlpTraceExporter(
                endpoint=otlp_endpoint,
                headers=otlp_headers,
                service_name=service_name,
            )
        )
    except (ImportError, ValueError):
        pass
    return ResilientMultiTraceExporter(exporters)


class AgentTraceService:
    def __init__(self, *, exporter):
        self.exporter = exporter

    def record(
        self,
        *,
        stage: str,
        status: str,
        duration_ms: int,
        trace_id: str,
        task_id: str,
        tenant_id: str,
        error_code: str | None = None,
    ) -> None:
        if stage not in ALLOWED_TRACE_STAGES:
            raise ValueError("E_AGENT_TRACE_STAGE:不支持的 Agent trace 阶段")
        record = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "log_type": "query_pilot_agent_trace",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": str(trace_id)[:128],
            "task_id": str(task_id)[:128],
            "tenant_id": str(tenant_id)[:128],
            "stage": stage,
            "status": "error" if status == "error" else "ok",
            "duration_ms": max(0, int(duration_ms)),
            "error_code": str(error_code)[:80] if error_code else None,
        }
        try:
            self.exporter.emit(record)
        except Exception:
            from app.metrics_runtime import record_trace_export_failure

            record_trace_export_failure()
            raise
        from app.metrics_runtime import record_agent_trace

        record_agent_trace(record)
    def wrap(
        self,
        stage: str,
        function: Callable,
        *,
        trace_id: str,
        task_id: str,
        tenant_id: str,
    ) -> Callable:
        if inspect.iscoroutinefunction(function):
            async def async_wrapped(*args, **kwargs):
                started = perf_counter()
                try:
                    result = await function(*args, **kwargs)
                except Exception as exc:
                    self._record_failure(stage, started, trace_id, task_id, tenant_id, exc)
                    raise
                self.record(
                    stage=stage,
                    status="ok",
                    duration_ms=int((perf_counter() - started) * 1000),
                    trace_id=trace_id,
                    task_id=task_id,
                    tenant_id=tenant_id,
                )
                return result

            return async_wrapped

        def wrapped(*args, **kwargs):
            started = perf_counter()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                self._record_failure(stage, started, trace_id, task_id, tenant_id, exc)
                raise
            self.record(
                stage=stage,
                status="ok",
                duration_ms=int((perf_counter() - started) * 1000),
                trace_id=trace_id,
                task_id=task_id,
                tenant_id=tenant_id,
            )
            return result

        return wrapped

    def _record_failure(
        self,
        stage: str,
        started: float,
        trace_id: str,
        task_id: str,
        tenant_id: str,
        exc: Exception,
    ) -> None:
        self.record(
            stage=stage,
            status="error",
            duration_ms=int((perf_counter() - started) * 1000),
            trace_id=trace_id,
            task_id=task_id,
            tenant_id=tenant_id,
            error_code=str(getattr(exc, "code", "E_AGENT_STAGE_FAILURE")),
        )


def summarize_agent_traces(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    records: list[dict[str, Any]] = []
    invalid_records = 0
    if target.exists():
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict) or value.get("log_type") != "query_pilot_agent_trace":
                        raise ValueError("invalid trace record")
                    records.append(value)
                except (json.JSONDecodeError, ValueError):
                    invalid_records += 1
    reflection_latencies = sorted(
        max(0, int(record.get("duration_ms", 0) or 0))
        for record in records
        if record.get("stage") == "reflect"
    )
    p95 = 0
    if reflection_latencies:
        index = max(0, min(len(reflection_latencies) - 1, (95 * len(reflection_latencies) + 99) // 100 - 1))
        p95 = reflection_latencies[index]
    bucket_bounds = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
    buckets = {
        str(bound): sum(duration <= bound for duration in reflection_latencies)
        for bound in bucket_bounds
    }
    buckets["+Inf"] = len(reflection_latencies)
    return {
        "spans": len(records),
        "invalid_records": invalid_records,
        "errors": sum(record.get("status") == "error" for record in records),
        "reflection_latency_avg_ms": round(sum(reflection_latencies) / len(reflection_latencies), 2) if reflection_latencies else 0,
        "reflection_latency_p95_ms": p95,
        "reflection_latency_count": len(reflection_latencies),
        "reflection_latency_sum_ms": sum(reflection_latencies),
        "reflection_latency_buckets": buckets,
    }


__all__ = [
    "ALLOWED_TRACE_STAGES",
    "AgentTraceService",
    "InMemoryTraceExporter",
    "JsonlTraceExporter",
    "OtlpTraceExporter",
    "ResilientMultiTraceExporter",
    "build_trace_exporter",
    "summarize_agent_traces",
    "TRACE_SCHEMA_VERSION",
]
