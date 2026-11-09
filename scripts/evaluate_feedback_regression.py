"""Replay an exported approved-feedback dataset against the current model."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent import build_sql_generator
from app.agent.langchain_adapter import generate_sql as generate_sql_langchain
from app.config import settings
from app.datasources import datasource_schema, execute_datasource, get_datasource
from app.feedback_regression import evaluate_feedback_regression
from app.llm import generate_sql
from app.model_defaults import DEFAULT_PROMPT_VERSION, compose_system_prompt
from app.model_operations_runtime import get_model_operations_service
from app.services.agent_query_service import AgentQueryService


def _load(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("feedback regression dataset is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("feedback regression dataset must contain a cases array")
    return payload


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _runtime_config(*, tenant_id: str, runtime_source: str):
    if runtime_source == "configured":
        return get_model_operations_service().resolve_runtime(tenant_id)
    return SimpleNamespace(
        provider_name="环境默认配置",
        adapter=settings.model_provider,
        base_url=settings.model_base_url,
        model=settings.model_name,
        api_key=settings.model_api_key,
        prompt_version=DEFAULT_PROMPT_VERSION,
        system_prompt=compose_system_prompt(),
    )


async def _run(dataset: dict, *, tenant_id: str, runtime_source: str = "environment") -> dict:
    runtime = _runtime_config(tenant_id=tenant_id, runtime_source=runtime_source)
    agent_service = AgentQueryService(
        provider=runtime.adapter,
        direct_generate=generate_sql,
        langchain_generate=generate_sql_langchain,
        build_langchain=build_sql_generator,
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        model=runtime.model,
        timeout=settings.model_timeout_seconds,
        system_prompt=runtime.system_prompt,
        prompt_version=runtime.prompt_version,
    )

    async def generate_current(**kwargs):
        return await agent_service.generate(
            question=kwargs["question"],
            schema=kwargs["schema"],
            context=kwargs.get("context"),
            dialect=kwargs["dialect"],
            direct_kwargs={
                "model": runtime.model,
                "timeout": settings.model_timeout_seconds,
                "api_key": runtime.api_key,
                "base_url": runtime.base_url,
                "cache_ttl": settings.sql_cache_ttl_seconds,
                "cache_max_entries": settings.sql_cache_max_entries,
                "system_prompt": runtime.system_prompt,
                "prompt_version": runtime.prompt_version,
            },
        )

    def resolve_datasource(case: dict) -> dict:
        datasource = get_datasource(str(case.get("datasource_id") or ""))
        if datasource is None:
            raise ValueError("E_DATASOURCE_NOT_FOUND")
        return datasource

    return await evaluate_feedback_regression(
        dataset=dataset,
        generate=generate_current if runtime.api_key else None,
        schema_for=lambda case: datasource_schema(resolve_datasource(case)),
        dialect_for=lambda case: str(resolve_datasource(case).get("backend") or "sqlite").lower(),
        execute=lambda case, sql, params: execute_datasource(
            resolve_datasource(case), sql, params, settings.max_rows
        ),
        model={
            "provider": runtime.provider_name,
            "model": runtime.model,
            "prompt_version": runtime.prompt_version,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate administrator-approved feedback cases")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/feedback-regression.json"))
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--runtime-source", choices=("environment", "configured"), default="environment")
    parser.add_argument("--require-external", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(
            _load(args.dataset),
            tenant_id=args.tenant_id,
            runtime_source=args.runtime_source,
        ))
        _write_atomic(args.output, report)
    except (OSError, ValueError) as exc:
        print(f"Feedback regression failed: {exc}")
        return 2
    except Exception:
        print("Feedback regression failed: unexpected runtime failure")
        return 2
    print(f"Feedback regression report written: {args.output}")
    if report["external_validation"] == "not_run":
        print("External model validation was not run because no current model API key is configured.")
        return 3 if args.require_external else 0
    if not report["gate"]["passed"]:
        for reason in report["gate"]["reason_codes"]:
            print(f"- {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
