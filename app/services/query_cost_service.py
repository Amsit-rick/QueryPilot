"""EXPLAIN based query cost guard.

The service deliberately estimates before execution and never treats returned
row count as scanned rows. Unsupported adapters are reported explicitly.
"""
from dataclasses import dataclass

from app.domain.data_gateway import GatewayCostEstimate, GatewayError, GatewayQuery


@dataclass(frozen=True)
class QueryCostDecision:
    estimate: GatewayCostEstimate
    allowed: bool
    reasons: tuple[str, ...] = ()


def evaluate_query_cost(
    estimate: GatewayCostEstimate,
    *,
    max_estimated_rows: int,
    max_estimated_cost: float,
    require_explain: bool = False,
) -> QueryCostDecision:
    reasons: list[str] = []
    if not estimate.supported:
        if require_explain:
            reasons.append("explain_unsupported")
        return QueryCostDecision(estimate, not reasons, tuple(reasons))
    if estimate.estimated_rows is not None and estimate.estimated_rows > max_estimated_rows:
        reasons.append("estimated_rows_limit")
    if estimate.estimated_cost is not None and estimate.estimated_cost > max_estimated_cost:
        reasons.append("estimated_cost_limit")
    if estimate.full_scan is True and estimate.estimated_rows is not None and estimate.estimated_rows > max_estimated_rows // 2:
        reasons.append("large_full_scan")
    return QueryCostDecision(estimate, not reasons, tuple(dict.fromkeys(reasons)))


def estimate_gateway_cost(service, identity: dict, gateway_id: str, *, sql: str, params: dict, max_rows: int, offset: int = 0) -> GatewayCostEstimate:
    return service.estimate_cost(identity, gateway_id, GatewayQuery(sql=sql, params=params, max_rows=max_rows, offset=offset))


__all__ = ["QueryCostDecision", "evaluate_query_cost", "estimate_gateway_cost"]
