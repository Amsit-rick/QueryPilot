from dataclasses import dataclass, field
from typing import Any

from .reflection import sanitize_reflection_text

@dataclass
class QueryAgentState:
    question: str
    sql: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    repair_attempts: int = 0
    max_repair_attempts: int = 2
    status: str = "initialized"
    intent: str = "data_query"
    confidence: float = 0.0
    needs_approval: bool = False
    semantic_context: dict[str, Any] = field(default_factory=dict)
    analysis_plan: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    reflection: dict[str, Any] = field(default_factory=dict)
    reflection_history: list[dict[str, Any]] = field(default_factory=list)
    reflection_budget: dict[str, Any] = field(default_factory=dict)
    derived_analysis: dict[str, Any] = field(default_factory=dict)
    explanation: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    thread_id: str = ""
    checkpoint: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        """Return a checkpoint-safe snapshot without parameter values."""
        error_summary = sanitize_reflection_text(self.error)
        if self.reflection.get("reason_codes"):
            error_summary = str(self.reflection["reason_codes"][0])[:80]
        return {
            "question": self.question,
            "sql": self.sql,
            "param_names": sorted(str(name) for name in self.params),
            "param_count": len(self.params),
            "error": error_summary,
            "error_code": self.error_code,
            "repair_attempts": self.repair_attempts,
            "max_repair_attempts": self.max_repair_attempts,
            "status": self.status,
            "intent": self.intent,
            "confidence": self.confidence,
            "needs_approval": self.needs_approval,
            "semantic_context": self.semantic_context,
            "analysis_plan": self.analysis_plan,
            "verification": self.verification,
            "reflection": self.reflection,
            "reflection_history": self.reflection_history,
            "reflection_budget": self.reflection_budget,
            "derived_analysis": self.derived_analysis,
            "explanation": self.explanation,
            "artifacts": self.artifacts,
            "thread_id": self.thread_id,
        }

    def runtime_payload(self) -> dict[str, Any]:
        """Return in-memory graph state, including values needed for execution."""
        return {**self.snapshot(), "params": dict(self.params)}
