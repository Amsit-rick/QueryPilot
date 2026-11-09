"""Evaluate the versioned synthetic business Question/SQL/Result dataset."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent import build_sql_generator  # noqa: E402
from app.agent.langchain_adapter import generate_sql as generate_sql_langchain  # noqa: E402
from app.business_gold_evaluation import evaluate_business_gold, load_business_gold_dataset  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import generate_sql  # noqa: E402
from app.model_defaults import DEFAULT_PROMPT_VERSION, compose_system_prompt  # noqa: E402
from app.services.agent_query_service import AgentQueryService  # noqa: E402


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


async def _run(dataset: dict) -> dict:
    service = AgentQueryService(
        provider=settings.model_provider,
        direct_generate=generate_sql,
        langchain_generate=generate_sql_langchain,
        build_langchain=build_sql_generator,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model=settings.model_name,
        timeout=settings.model_timeout_seconds,
        system_prompt=compose_system_prompt(),
        prompt_version=DEFAULT_PROMPT_VERSION,
    )

    async def generate_current(**kwargs):
        return await service.generate(
            question=kwargs["question"],
            schema=kwargs["schema"],
            context=kwargs.get("context"),
            dialect=kwargs["dialect"],
            direct_kwargs={
                "model": settings.model_name,
                "timeout": settings.model_timeout_seconds,
                "api_key": settings.model_api_key,
                "base_url": settings.model_base_url,
                "cache_ttl": settings.sql_cache_ttl_seconds,
                "cache_max_entries": settings.sql_cache_max_entries,
                "system_prompt": compose_system_prompt(),
                "prompt_version": DEFAULT_PROMPT_VERSION,
            },
        )

    return await evaluate_business_gold(
        dataset=dataset,
        generate=generate_current if settings.model_api_key else None,
        model={
            "provider": "环境默认配置",
            "model": settings.model_name,
            "prompt_version": DEFAULT_PROMPT_VERSION,
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "data" / "skill_query_eval.json")
    parser.add_argument("--output", type=Path, default=Path("artifacts/business-gold-eval.json"))
    parser.add_argument("--require-external", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(load_business_gold_dataset(args.dataset)))
        _write_atomic(args.output, report)
    except (OSError, ValueError) as exc:
        print(f"Business gold evaluation failed: {exc}")
        return 2
    except Exception:
        print("Business gold evaluation failed: unexpected runtime failure")
        return 2
    print(f"Business gold report written: {args.output}")
    if not report["local_gate"]["passed"]:
        return 1
    if report["external_validation"] == "not_run":
        print("External model validation was not run because no model API key is configured.")
        return 3 if args.require_external else 0
    return 0 if report["external_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
