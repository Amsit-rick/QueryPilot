import pytest
import asyncio
import sys
import types
from app.agent.graph import QueryAgentGraph
from app.agent.state import QueryAgentState
from app.agent.checkpoint import save_checkpoint, load_checkpoint
from app.agent.langgraph_adapter import build_langgraph
from app.agent.runtime import create_query_graph
from app.agent.contracts import AnalysisPlan, VerificationResult
from app.agent.reflection import AgentTerminalError
import app.agent.runtime as runtime_module
from app import approval
from app.guardrails import QueryError, validate_sql

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_completion_checkpoint_failure_never_reexecutes_query(runtime):
  async def run():
    executions = []
    failures = []
    def checkpoint(snapshot):
      if snapshot["status"] == "completed" and not failures:
        failures.append(True)
        raise OSError("checkpoint unavailable")
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result="SELECT 1"),
      validate=lambda sql: sql,
      execute=lambda sql, _: executions.append(sql),
      checkpoint=checkpoint,
    )
    state = QueryAgentState("test")
    with pytest.raises(OSError, match="checkpoint unavailable"):
      await graph.ainvoke(state)
    assert executions == ["SELECT 1"]
    assert state.sql == "SELECT 1"
    assert state.status == "checkpoint_failed"
    assert state.error == "checkpoint unavailable"
  asyncio.run(run())

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_successful_repair_clears_previous_error(runtime):
  async def run():
    generations = iter(["bad", "SELECT 1"])
    def validate(sql):
      if sql == "bad":
        raise ValueError("invalid SQL")
      return sql
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result=next(generations)),
      validate=validate,
      execute=lambda *_: 1,
    )
    result = await graph.ainvoke(QueryAgentState("test"))
    assert result["state"].status == "completed"
    assert result["state"].error == ""
  asyncio.run(run())

def test_graph_retries_after_validation_error():
  async def run():
    calls = {"generate": 0, "validate": 0}
    async def generate(question):
        calls["generate"] += 1
        return "bad" if calls["generate"] == 1 else "SELECT 1"
    def validate(sql):
        calls["validate"] += 1
        if sql == "bad": raise ValueError("invalid sql")
        return sql
    graph = QueryAgentGraph(generate=generate, validate=validate, execute=lambda sql, params: {"ok": True})
    result = await graph.ainvoke(QueryAgentState("test"))
    assert result["state"].status == "completed"
    assert result["state"].repair_attempts == 1
  asyncio.run(run())

def test_graph_rejects_multistatement_without_entering_reflection_repair_loop():
  async def run():
    generated_questions = []
    executed = []
    async def generate(question):
      generated_questions.append(question)
      return "SELECT COUNT(*) FROM sales; DELETE FROM sales"
    graph = QueryAgentGraph(
      generate=generate,
      validate=lambda sql: validate_sql(sql, {"sales": {"region"}}),
      execute=lambda sql, _: executed.append(sql) or ([], [[7]], False),
    )
    state = QueryAgentState("统计销售记录总数")
    with pytest.raises(AgentTerminalError, match="E_READONLY_VIOLATION"):
      await graph.ainvoke(state)
    assert len(generated_questions) == 1
    assert executed == []
    assert state.status == "failed"
    assert state.reflection["failure_type"] == "security"
    assert state.reflection["action"] == "fail"
    with pytest.raises(QueryError, match="仅允许执行单条 SELECT 语句"):
      validate_sql("SELECT COUNT(*) FROM sales; DELETE FROM sales", {"sales": {"region"}})
  asyncio.run(run())

def test_graph_has_bounded_retries():
  async def run():
    async def generate(question): return "bad"
    graph = QueryAgentGraph(generate=generate, validate=lambda sql: (_ for _ in ()).throw(ValueError("invalid")), execute=lambda *_: None, max_repairs=2)
    with pytest.raises(ValueError): await graph.ainvoke(QueryAgentState("test"))
  asyncio.run(run())

def test_graph_retries_after_retryable_result_verification_failure():
  async def run():
    calls = {"generate": 0, "verify": 0}
    async def generate(_):
      calls["generate"] += 1
      return f"SELECT {calls['generate']}"
    def verify(_):
      calls["verify"] += 1
      return VerificationResult(accepted=calls["verify"] > 1, reason="empty result")
    graph = QueryAgentGraph(generate=generate, validate=lambda sql: sql, execute=lambda sql, _: sql, verify=verify)
    result = await graph.ainvoke(QueryAgentState("test"))
    assert result["result"] == "SELECT 2"
    assert result["state"].repair_attempts == 1
    assert result["state"].verification["accepted"] is True
  asyncio.run(run())

def test_graph_does_not_retry_non_retryable_verification_failure():
  async def run():
    calls = {"generate": 0}
    async def generate(_):
      calls["generate"] += 1
      return "SELECT 1"
    graph = QueryAgentGraph(generate=generate, validate=lambda sql: sql, execute=lambda *_: {}, verify=lambda _: VerificationResult(accepted=False, reason="malformed", retryable=False))
    with pytest.raises(ValueError, match="E_RESULT_VERIFICATION"):
      await graph.ainvoke(QueryAgentState("test"))
    assert calls["generate"] == 1
  asyncio.run(run())

def test_graph_emits_serializable_checkpoints():
  async def run():
    snapshots = []
    async def generate(question): return "SELECT 1"
    graph = QueryAgentGraph(
        generate=generate,
        validate=lambda sql: sql,
        execute=lambda sql, params: {"ok": True},
        checkpoint=snapshots.append,
    )
    state = QueryAgentState("test", thread_id="thread-1")
    await graph.ainvoke(state)
    assert snapshots[-1]["status"] == "completed"
    assert snapshots[-1]["thread_id"] == "thread-1"
  asyncio.run(run())

def test_graph_resolves_semantics_and_plans_once_before_repair_loop():
  async def run():
    calls = []
    generations = {"count": 0}
    def resolve(question):
      calls.append(("resolve", question))
      return {"catalog_version": "v2", "metrics": [{"name": "GMV"}]}
    def plan(question, context):
      calls.append(("plan", context["catalog_version"]))
      return AnalysisPlan(objective=question, intent="metric_query", steps=["generate_sql", "validate_sql", "execute_query", "verify_result", "analyze_result"], requires_multiple_steps=True, analysis_mode="descriptive_statistics")
    async def generate(_):
      generations["count"] += 1
      return "bad" if generations["count"] == 1 else "SELECT 1"
    graph = QueryAgentGraph(
      generate=generate,
      validate=lambda sql: (_ for _ in ()).throw(ValueError("invalid")) if sql == "bad" else sql,
      execute=lambda *_: {"ok": True},
      resolve_semantics=resolve,
      plan=plan,
    )
    result = await graph.ainvoke(QueryAgentState("GMV trend"))
    assert calls == [("resolve", "GMV trend"), ("plan", "v2")]
    assert result["state"].semantic_context["catalog_version"] == "v2"
    assert result["state"].analysis_plan["intent"] == "metric_query"
    assert result["state"].analysis_plan["requires_multiple_steps"] is True
  asyncio.run(run())

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_result_postprocessing_runs_in_order_without_reexecuting_query(runtime):
  async def run():
    calls = []
    result = (["email"], [["m***@example.com"]], False)
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result="SELECT email FROM users"),
      validate=lambda sql: sql,
      execute=lambda *_: calls.append("execute") or result,
      verify=lambda value: calls.append(("verify", value[1][0][0])) or VerificationResult(accepted=True),
      explain=lambda value: calls.append(("explain", value[1][0][0])) or {"summary": "1 row", "row_count": 1},
      materialize=lambda value, explanation: calls.append(("materialize", value[1][0][0])) or {"name": "result", "idempotency_key": "task:table", "metadata": {"summary": explanation["summary"]}},
    )
    output = await graph.ainvoke(QueryAgentState("test"))
    assert calls == ["execute", ("verify", "m***@example.com"), ("explain", "m***@example.com"), ("materialize", "m***@example.com")]
    assert output["state"].explanation["method"] == "deterministic"
    assert output["state"].artifacts[0]["idempotency_key"] == "task:table"
  asyncio.run(run())

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_postprocessing_failure_never_reexecutes_query(runtime):
  async def run():
    executions = []
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result="SELECT 1"),
      validate=lambda sql: sql,
      execute=lambda *_: executions.append(True) or (["value"], [[1]], False),
      explain=lambda _: (_ for _ in ()).throw(RuntimeError("explanation unavailable")),
    )
    state = QueryAgentState("test")
    with pytest.raises(RuntimeError, match="explanation unavailable"):
      await graph.ainvoke(state)
    assert executions == [True]
    assert state.status == "postprocess_failed"
  asyncio.run(run())

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_multistep_plan_executes_derived_analysis_after_verification(runtime):
  async def run():
    calls = []
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result="SELECT amount FROM sales"),
      validate=lambda sql: sql,
      execute=lambda *_: calls.append("execute") or (["amount"], [[10], [20]], False),
      plan=lambda question, _: AnalysisPlan(objective=question, steps=["generate_sql", "validate_sql", "execute_query", "verify_result", "analyze_result"], requires_multiple_steps=True, analysis_mode="descriptive_statistics"),
      verify=lambda _: calls.append("verify") or VerificationResult(accepted=True),
      analyze=lambda result, plan: calls.append("analyze") or {"mode": plan["analysis_mode"], "summary": "done", "metrics": {"amount": {"count": 2, "average": 15}}},
      explain=lambda _: calls.append("explain") or {"summary": "done", "row_count": 2},
    )
    output = await graph.ainvoke(QueryAgentState("两步分析"))
    assert calls == ["execute", "verify", "analyze", "explain"]
    assert output["state"].derived_analysis["metrics"]["amount"]["average"] == 15
    assert output["state"].explanation["summary"] == "done"
  asyncio.run(run())

def test_redis_checkpoint_roundtrip(monkeypatch):
  class FakeRedis:
    values = {}
    def setex(self, key, ttl, value): self.values[key] = value
    def get(self, key): return self.values.get(key)
  fake = FakeRedis()
  monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: fake)))
  assert save_checkpoint("redis://test", "t1", {"status": "completed"})
  assert load_checkpoint("redis://test", "t1")["status"] == "completed"

def test_langgraph_adapter_is_optional():
  graph = build_langgraph(generate=lambda _: "SELECT 1", validate=lambda sql: sql, execute=lambda *_: {})
  assert graph is None or hasattr(graph, "ainvoke")

def test_langgraph_runtime_executes_real_graph():
  async def run():
    graph = create_query_graph(
        runtime="langgraph",
        generate=lambda _: asyncio.sleep(0, result="SELECT 1"),
        validate=lambda sql: sql,
        execute=lambda sql, params: (sql, params),
    )
    result = await graph.ainvoke(QueryAgentState("test", params={"x": 1}))
    assert result["state"].status == "completed"
    assert result["result"] == ("SELECT 1", {"x": 1})
  asyncio.run(run())

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_graph_executes_model_generated_typed_params_without_checkpoint_values(runtime):
  async def run():
    snapshots = []
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result={"sql": "SELECT id FROM sales WHERE region = :region", "params": {"region": "sensitive-region-value"}}),
      validate=lambda sql: sql,
      execute=lambda sql, params: (sql, params),
      checkpoint=snapshots.append,
    )
    output = await graph.ainvoke(QueryAgentState("查询指定区域销售"))
    assert output["result"][1] == {"region": "sensitive-region-value"}
    assert all("params" not in snapshot for snapshot in snapshots)
    assert any(snapshot.get("param_names") == ["region"] for snapshot in snapshots)
    assert "sensitive-region-value" not in str(snapshots)
  asyncio.run(run())

@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_graph_rejects_generated_param_conflict(runtime):
  async def run():
    graph = create_query_graph(
      runtime=runtime,
      generate=lambda _: asyncio.sleep(0, result={"sql": "SELECT id FROM sales WHERE region = :region", "params": {"region": "华南"}}),
      validate=lambda sql: sql,
      execute=lambda sql, params: (sql, params),
      max_repairs=0,
    )
    with pytest.raises(ValueError, match="E_QUERY_PARAM_CONFLICT"):
      await graph.ainvoke(QueryAgentState("华东销售", params={"region": "华东"}))
  asyncio.run(run())

def test_langgraph_checkpoints_each_node_without_raw_result():
  async def run():
    snapshots = []
    graph = create_query_graph(
        runtime="langgraph",
        generate=lambda _: asyncio.sleep(0, result="SELECT 1"),
        validate=lambda sql: sql,
        execute=lambda *_: (["value"], [[1]], False),
        checkpoint=snapshots.append,
    )
    await graph.ainvoke(QueryAgentState("test"))
    statuses = [snapshot["status"] for snapshot in snapshots]
    assert {"semantics_resolved", "planned", "generated", "validated", "executed", "verified", "explained", "completed"}.issubset(statuses)
    assert all("result" not in snapshot for snapshot in snapshots)
  asyncio.run(run())

def test_langgraph_checkpoint_failure_preserves_sql_without_reexecuting():
  async def run():
    executions = []
    snapshots = []
    def checkpoint(snapshot):
      snapshots.append(snapshot)
      if snapshot.get("status") == "executed":
        raise OSError("checkpoint unavailable")
    graph = create_query_graph(
        runtime="langgraph",
        generate=lambda _: asyncio.sleep(0, result="SELECT 1"),
        validate=lambda sql: sql,
        execute=lambda sql, _: executions.append(sql) or (["value"], [[1]], False),
        checkpoint=checkpoint,
    )
    state = QueryAgentState("test")
    with pytest.raises(OSError, match="checkpoint unavailable"):
      await graph.ainvoke(state)
    assert executions == ["SELECT 1"]
    assert state.sql == "SELECT 1"
    assert state.status == "checkpoint_failed"
    assert state.error == "checkpoint unavailable"
    assert all("result" not in snapshot for snapshot in snapshots)
  asyncio.run(run())

def test_langgraph_runtime_exposes_semantics_and_analysis_plan():
  async def run():
    graph = create_query_graph(
      runtime="langgraph",
      generate=lambda _: asyncio.sleep(0, result="SELECT 1"),
      validate=lambda sql: sql,
      execute=lambda *_: {"ok": True},
      resolve_semantics=lambda _: {"catalog_version": "v3"},
      plan=lambda question, _: {"objective": question, "intent": "trend", "steps": ["generate_sql", "validate_sql", "execute_query", "verify_result"]},
    )
    result = await graph.ainvoke(QueryAgentState("trend"))
    assert result["state"].semantic_context == {"catalog_version": "v3"}
    assert result["state"].analysis_plan["intent"] == "trend"
  asyncio.run(run())

def test_langgraph_runtime_has_bounded_repair_loop():
  async def run():
    calls = {"count": 0}
    async def generate(_):
      calls["count"] += 1
      return "bad"
    graph = create_query_graph(runtime="langgraph", generate=generate, validate=lambda _: (_ for _ in ()).throw(ValueError("invalid")), execute=lambda *_: None, max_repairs=2)
    with pytest.raises(ValueError, match="E_QUERY_ERROR"):
      await graph.ainvoke(QueryAgentState("test"))
    assert calls["count"] == 3
  asyncio.run(run())

def test_langgraph_runtime_retries_after_verification_failure():
  async def run():
    verification_calls = {"count": 0}
    def verify(_):
      verification_calls["count"] += 1
      return {"accepted": verification_calls["count"] > 1, "reason": "retry"}
    graph = create_query_graph(runtime="langgraph", generate=lambda _: asyncio.sleep(0, result={"sql": "SELECT 1", "intent": "summary", "confidence": 0.9}), validate=lambda sql: sql, execute=lambda *_: {"ok": True}, verify=verify, max_repairs=2)
    result = await graph.ainvoke(QueryAgentState("test"))
    assert result["state"].status == "completed"
    assert result["state"].intent == "summary"
    assert result["state"].verification["accepted"] is True
  asyncio.run(run())

def test_configured_langgraph_never_silently_falls_back(monkeypatch):
  monkeypatch.setattr(runtime_module, "build_langgraph", lambda **kwargs: None)
  with pytest.raises(RuntimeError, match="未安装 langgraph"):
    create_query_graph(runtime="langgraph", generate=lambda _: None, validate=lambda sql: sql, execute=lambda *_: None)

def test_approval_store_is_tenant_isolated():
  owner = {"subject": "1", "tenant_id": "a"}
  other = {"subject": "2", "tenant_id": "b"}
  item = approval.create(identity=owner, question="q", sql="SELECT 1", params={}, datasource_id="d")
  assert approval.get_for(item["id"], owner)["status"] == "pending"
  assert approval.get_for(item["id"], other) is None
