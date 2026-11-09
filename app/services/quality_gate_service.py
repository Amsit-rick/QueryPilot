"""Deterministic quality gates that reuse the query approval workflow."""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class ApprovalGateDecision:
    required: bool
    reasons: tuple[str, ...] = ()
    confidence: float | None = None


class ApprovalGateRequired(RuntimeError):
    approval_gate = True

    def __init__(self, *, sql: str, reasons: tuple[str, ...], confidence: float | None, analysis_mode: str = "none"):
        super().__init__("查询需要人工确认")
        self.sql = sql
        self.reasons = reasons
        self.confidence = confidence
        self.analysis_mode = analysis_mode


def evaluate_pre_execution(
    sql: str,
    *,
    confidence: float,
    model_requires_approval: bool,
    requested_rows: int,
    low_confidence_threshold: float,
    high_risk_row_threshold: int,
) -> ApprovalGateDecision:
    reasons: list[str] = []
    normalized_confidence = confidence if 0 < confidence <= 1 else None
    if model_requires_approval:
        reasons.append("model_requested")
    if normalized_confidence is not None and normalized_confidence < low_confidence_threshold:
        reasons.append("low_confidence")
    try:
        statement = sqlglot.parse_one(sql)
    except sqlglot.errors.ParseError:
        statement = None
    if statement is not None:
        select = statement.find(exp.Select)
        projections = select.expressions if select is not None else []
        if any(
            isinstance(projection, exp.Star)
            or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))
            for projection in projections
        ):
            reasons.append("wildcard_projection")
    if requested_rows > high_risk_row_threshold:
        reasons.append("large_result_request")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return ApprovalGateDecision(bool(unique_reasons), unique_reasons, normalized_confidence)


def anomaly_gate_reasons(analysis: dict | None) -> tuple[str, ...]:
    if analysis and analysis.get("anomalies"):
        return ("candidate_anomaly",)
    return ()


def relationship_gate_reasons(sql: str, relationships: list[dict] | None, *, min_confidence: float = 0.8, require_approved: bool = True) -> tuple[str, ...]:
    """Require confirmation for joins backed by unapproved/low-confidence relations."""
    if not relationships:
        return ()
    try:
        statement = sqlglot.parse_one(sql)
        tables = {str(table.name).lower() for table in statement.find_all(exp.Table)}
    except (sqlglot.errors.ParseError, AttributeError):
        return ()
    for relation in relationships:
        if str(relation.get("from_table", "")).lower() not in tables or str(relation.get("to_table", "")).lower() not in tables:
            continue
        confidence = relation.get("confidence", 0)
        approved = bool(relation.get("approved", False))
        if (require_approved and not approved) or confidence < min_confidence:
            return ("relationship_confirmation",)
    return ()


__all__ = ["ApprovalGateDecision", "ApprovalGateRequired", "anomaly_gate_reasons", "evaluate_pre_execution", "relationship_gate_reasons"]
