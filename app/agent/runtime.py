"""Runtime selection for the query graph."""
from collections.abc import Callable
from typing import Any

from .graph import QueryAgentGraph
from .langgraph_adapter import build_langgraph
from .reflection import ReflectionPolicy


def create_query_graph(*, runtime: str, generate: Callable, validate: Callable, execute: Callable, resolve_semantics: Callable | None = None, plan: Callable | None = None, verify: Callable | None = None, analyze: Callable | None = None, explain: Callable | None = None, materialize: Callable | None = None, pre_execute: Callable | None = None, max_repairs: int = 2, checkpoint: Callable[[dict[str, Any]], None] | None = None, reflection_policy: ReflectionPolicy | None = None, max_schema_expansions: int = 1, max_execution_retries: int = 1, max_reflection_steps: int = 12):
    if runtime == "langgraph":
        graph = build_langgraph(generate=generate, validate=validate, execute=execute, resolve_semantics=resolve_semantics, plan=plan, verify=verify, analyze=analyze, explain=explain, materialize=materialize, pre_execute=pre_execute, max_repairs=max_repairs, checkpoint=checkpoint, reflection_policy=reflection_policy, max_schema_expansions=max_schema_expansions, max_execution_retries=max_execution_retries, max_reflection_steps=max_reflection_steps)
        if graph is None:
            raise RuntimeError("AGENT_RUNTIME=langgraph，但未安装 langgraph 依赖")
        return graph
    return QueryAgentGraph(generate=generate, validate=validate, execute=execute, resolve_semantics=resolve_semantics, plan=plan, verify=verify, analyze=analyze, explain=explain, materialize=materialize, pre_execute=pre_execute, max_repairs=max_repairs, checkpoint=checkpoint, reflection_policy=reflection_policy, max_schema_expansions=max_schema_expansions, max_execution_retries=max_execution_retries, max_reflection_steps=max_reflection_steps)
