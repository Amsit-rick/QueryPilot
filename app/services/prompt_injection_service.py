"""High-confidence prompt-injection screening before SQL generation.

This is a defense-in-depth signal. Database permissions, parameter binding and
the SQL AST guardrail remain the authoritative execution boundary.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptInjectionDecision:
    blocked: bool
    reasons: tuple[str, ...] = ()
    fingerprint: str = ""


class PromptInjectionError(ValueError):
    code = "E_PROMPT_INJECTION"
    retryable = False

    def __init__(self, decision: PromptInjectionDecision):
        self.decision = decision
        super().__init__("检测到试图绕过查询安全边界的指令，请改为描述需要查询的业务数据")


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"(?:忽略|无视|覆盖|绕过|取消).{0,24}(?:系统|开发者|安全|先前|以上).{0,12}(?:指令|提示词|规则|限制)"
            r"|(?:ignore|disregard|override|bypass).{0,32}(?:system|developer|previous|safety).{0,16}(?:prompt|instruction|rule)",
            re.I | re.S,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"(?:显示|泄露|输出|打印|告诉我|提取).{0,24}(?:system\s*prompt|系统提示词|api\s*key|密钥|token|令牌|密码|连接串|dsn|环境变量)"
            r"|(?:reveal|leak|print|dump|exfiltrat\w*).{0,32}(?:system\s*prompt|api\s*key|secret|token|password|connection\s*string|environment)",
            re.I | re.S,
        ),
    ),
    (
        "tool_escape",
        re.compile(
            r"(?:调用|执行|运行|使用).{0,18}(?:shell|cmd|powershell|bash|终端|命令行|文件系统|网络请求|任意工具)"
            r"|(?:run|execute|invoke|use).{0,18}(?:shell|cmd|powershell|bash|terminal|filesystem|network|tool)",
            re.I | re.S,
        ),
    ),
    (
        "safety_bypass",
        re.compile(
            r"(?:绕过|关闭|禁用|跳过).{0,24}(?:sql|ast|只读|白名单|审批|安全|校验|权限|限制)"
            r"|(?:bypass|disable|skip).{0,24}(?:sql|ast|read.?only|allowlist|approval|guardrail|validation|permission)",
            re.I | re.S,
        ),
    ),
    (
        "write_request",
        re.compile(
            r"(?:生成|执行|给我|运行).{0,18}(?:insert|update|delete|drop|alter|truncate|create|grant|revoke)\b"
            r"|(?:generate|execute|run|write).{0,18}(?:insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
            re.I | re.S,
        ),
    ),
)


def inspect_prompt(question: str) -> PromptInjectionDecision:
    normalized = unicodedata.normalize("NFKC", str(question or ""))
    normalized = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", normalized)
    normalized = " ".join(normalized.split())[:4000]
    reasons = []
    for name, pattern in _RULES:
        matches = list(pattern.finditer(normalized))
        if name == "secret_exfiltration":
            matches = [
                match for match in matches
                if not re.search(r"(?:不要|不得|禁止|无需|别)\s*$", normalized[max(0, match.start() - 8):match.start()], re.I)
            ]
        if matches:
            reasons.append(name)
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
    return PromptInjectionDecision(blocked=bool(reasons), reasons=tuple(reasons), fingerprint=fingerprint)


def ensure_safe_generation_prompt(question: str) -> PromptInjectionDecision:
    decision = inspect_prompt(question)
    if decision.blocked:
        raise PromptInjectionError(decision)
    return decision


def sanitize_untrusted_context(value: Any, *, source: str) -> tuple[Any, list[dict[str, Any]]]:
    """Recursively replace injection-like strings and retain only safe evidence."""
    findings: list[dict[str, Any]] = []

    def sanitize(item: Any, path: str) -> Any:
        if isinstance(item, dict):
            return {str(key): sanitize(child, f"{path}.{key}") for key, child in item.items()}
        if isinstance(item, list):
            return [sanitize(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if isinstance(item, tuple):
            return [sanitize(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if not isinstance(item, str):
            return item
        decision = inspect_prompt(item)
        if not decision.blocked:
            return item
        findings.append(
            {
                "source": str(source)[:64],
                "path": path[:256],
                "reason_codes": list(decision.reasons),
                "fingerprint": decision.fingerprint,
            }
        )
        return "[blocked untrusted context]"

    return sanitize(value, "$"), findings
