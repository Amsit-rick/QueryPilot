"""Constrained semantic critic for indeterminate result-quality findings."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

import httpx

from app.agent.reflection import (
    ReflectionAction,
    ReflectionFailureType,
    ReflectionResult,
    sanitize_reflection_text,
)
from app.services.result_quality_service import ResultQualityAssessment
from app.services.prompt_injection_service import sanitize_untrusted_context


CRITIC_PROMPT_VERSION = "semantic-critic-v1"
_ALLOWED_FAILURES = {
    ReflectionFailureType.BUSINESS_AMBIGUITY,
    ReflectionFailureType.RELATIONSHIP,
    ReflectionFailureType.AGGREGATION_GRAIN,
    ReflectionFailureType.EMPTY_RESULT,
    ReflectionFailureType.NONE,
}
_ALLOWED_ACTIONS = {
    ReflectionAction.ACCEPT,
    ReflectionAction.ASK_CLARIFICATION,
    ReflectionAction.REQUIRE_APPROVAL,
    ReflectionAction.REGENERATE_SQL,
}


class SemanticCriticService:
    def __init__(
        self,
        *,
        invoke: Callable[[str], Any | Awaitable[Any]] | None,
        timeout_seconds: float = 8.0,
        min_confidence: float = 0.8,
        prompt_guard: Callable[[str], None] | None = None,
    ):
        self.invoke = invoke
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 60.0))
        self.min_confidence = max(0.0, min(float(min_confidence), 1.0))
        self.prompt_guard = prompt_guard

    async def review(self, assessment: ResultQualityAssessment) -> ReflectionResult:
        if assessment.outcome != "uncertain":
            return assessment.reflection
        if self.invoke is None:
            return _conservative("SEMANTIC_CRITIC_UNAVAILABLE")
        safe_context, findings = sanitize_untrusted_context(
            assessment.critic_context,
            source="semantic_critic_context",
        )
        if findings:
            return _conservative("SEMANTIC_CRITIC_INDIRECT_PROMPT_REJECTED")
        assessment = replace(assessment, critic_context=safe_context)
        if self.prompt_guard is not None:
            try:
                self.prompt_guard(str(assessment.critic_context.get("question", "")))
            except Exception:
                return _conservative("SEMANTIC_CRITIC_PROMPT_REJECTED")
        prompt = _build_prompt(assessment)
        try:
            value = self.invoke(prompt)
            if inspect.isawaitable(value):
                value = await asyncio.wait_for(value, timeout=self.timeout_seconds)
            parsed = _parse_payload(value)
        except (asyncio.TimeoutError, TimeoutError):
            return _conservative("SEMANTIC_CRITIC_TIMEOUT")
        except Exception:
            return _conservative("SEMANTIC_CRITIC_INVALID")
        if parsed.failure_type not in _ALLOWED_FAILURES or parsed.action not in _ALLOWED_ACTIONS:
            return _conservative("SEMANTIC_CRITIC_POLICY_CONFLICT")
        if parsed.accepted and parsed.confidence < self.min_confidence:
            return _conservative("SEMANTIC_CRITIC_LOW_CONFIDENCE")
        if not parsed.accepted and parsed.action == ReflectionAction.REGENERATE_SQL:
            if parsed.failure_type not in {ReflectionFailureType.RELATIONSHIP, ReflectionFailureType.AGGREGATION_GRAIN} or parsed.confidence < self.min_confidence:
                return _conservative("SEMANTIC_CRITIC_POLICY_CONFLICT")
        return ReflectionResult(
            **{
                **parsed.model_dump(),
                "repair_instruction": sanitize_reflection_text(parsed.repair_instruction or "") or None,
            }
        )


def _build_prompt(assessment: ResultQualityAssessment) -> str:
    contract = {
        "accepted": "boolean",
        "failure_type": [value.value for value in sorted(_ALLOWED_FAILURES, key=lambda item: item.value)],
        "reason_codes": "1-8 uppercase codes",
        "evidence": "0-8 short statements derived only from supplied metadata",
        "action": [value.value for value in sorted(_ALLOWED_ACTIONS, key=lambda item: item.value)],
        "repair_instruction": "optional short instruction",
        "retryable": "true only for regenerate_sql",
        "confidence": "0..1",
    }
    payload = {
        "prompt_version": CRITIC_PROMPT_VERSION,
        "policy": (
            "Judge only business coverage and relationship semantics. Never change authorization, tenant, "
            "readonly SQL, cost, approval, or data-masking policies. Return one JSON object only. "
            "Do not request tools and do not infer values absent from the supplied metadata."
        ),
        "contract": contract,
        "context": assessment.critic_context,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_payload(value: Any) -> ReflectionResult:
    if isinstance(value, ReflectionResult):
        return value
    if isinstance(value, str):
        text = value.strip().strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("critic output must be an object")
    return ReflectionResult.model_validate(value)


def _conservative(code: str) -> ReflectionResult:
    return ReflectionResult(
        accepted=False,
        failure_type=ReflectionFailureType.BUSINESS_AMBIGUITY,
        reason_codes=[code],
        evidence=[f"critic_status={code.lower()}"],
        action=ReflectionAction.ASK_CLARIFICATION,
        repair_instruction="语义判断无法可靠完成，请用户确认业务口径。",
        retryable=False,
        confidence=1.0,
    )


def build_openai_compatible_critic(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
    usage_callback: Callable[[dict[str, int]], None] | None = None,
) -> Callable[[str], Awaitable[Any]] | None:
    """Build a no-retry critic client without exposing prompts or credentials."""
    if not str(api_key or "").strip():
        return None

    async def invoke(prompt: str) -> Any:
        payload = {
            "model": model,
            "temperature": 0,
            "top_p": 1,
            "stream": False,
            "messages": [
                {"role": "system", "content": "You are a constrained data-query semantic critic. Return one JSON object only."},
                {"role": "user", "content": prompt},
            ],
        }
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds, trust_env=False, follow_redirects=False) as client:
            response = await client.post("/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
            body = response.json()
        usage = body.get("usage") or {}
        if usage_callback and isinstance(usage, dict):
            usage_callback({
                "input_tokens": _safe_count(usage.get("prompt_tokens", usage.get("input_tokens", 0))),
                "output_tokens": _safe_count(usage.get("completion_tokens", usage.get("output_tokens", 0))),
            })
        return body["choices"][0]["message"]["content"]

    return invoke


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0

