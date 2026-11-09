import asyncio

import pytest
from pydantic import ValidationError

from app.agent.reflection import (
    AgentTerminalError,
    ReflectionAction,
    ReflectionBudget,
    ReflectionFailureType,
    ReflectionPolicy,
    ReflectionResult,
    sanitize_reflection_text,
)
from app.agent.runtime import create_query_graph
from app.agent.state import QueryAgentState
from app.agent.contracts import VerificationResult
from app.guardrails import QueryError


class RetryableExecutionError(RuntimeError):
    code = "E_TIMEOUT"
    retryable = True


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_runtime_applies_async_embedded_result_reflection(runtime):
    async def run():
        generations = []

        async def generate(_question):
            sql = "SELECT region FROM sales" if not generations else "SELECT region FROM sales ORDER BY region DESC LIMIT 5"
            generations.append(sql)
            return sql

        async def verify(output):
            if len(generations) == 1:
                reflection = ReflectionResult(
                    accepted=False,
                    failure_type=ReflectionFailureType.AGGREGATION_GRAIN,
                    reason_codes=["TOP_N_ORDER_MISSING"],
                    evidence=["top_n request has no ORDER BY"],
                    action=ReflectionAction.REGENERATE_SQL,
                    repair_instruction="补齐排序和 LIMIT。",
                    retryable=True,
                    confidence=1.0,
                )
                return VerificationResult(
                    accepted=False,
                    reason="结果排序不符合问题要求",
                    retryable=True,
                    source="deterministic",
                    reflection=reflection.model_dump(mode="json"),
                )
            return VerificationResult(accepted=True, source="deterministic")

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda sql: sql,
            execute=lambda sql, _params: sql,
            verify=verify,
            max_repairs=2,
        )
        output = await graph.ainvoke(QueryAgentState("前 5 个区域"))

        assert output["state"].status == "completed"
        assert len(generations) == 2
        assert output["state"].reflection_history[0]["reason_codes"] == ["TOP_N_ORDER_MISSING"]

    asyncio.run(run())


def test_reflection_result_rejects_conflicting_acceptance_and_action():
    with pytest.raises(ValidationError):
        ReflectionResult(
            accepted=True,
            failure_type=ReflectionFailureType.NONE,
            reason_codes=["OK"],
            evidence=[],
            action=ReflectionAction.REGENERATE_SQL,
            retryable=False,
            confidence=1.0,
        )


def test_reflection_policy_never_retries_security_or_caller_sql():
    policy = ReflectionPolicy()

    security = policy.from_failure(
        stage="validate",
        error_code="E_READONLY_VIOLATION",
        message="仅允许只读查询",
        retryable=True,
        provided_sql=False,
        budget=ReflectionBudget(max_sql_repairs=2),
    )
    caller_sql = policy.from_failure(
        stage="validate",
        error_code="E_QUERY_INVALID",
        message="SQL 语法无效",
        retryable=True,
        provided_sql=True,
        budget=ReflectionBudget(max_sql_repairs=2),
    )

    assert security.failure_type == ReflectionFailureType.SECURITY
    assert security.action == ReflectionAction.FAIL
    assert security.retryable is False
    assert caller_sql.action == ReflectionAction.FAIL
    assert caller_sql.reason_codes == ["CALLER_SQL_REJECTED"]


def test_reflection_policy_fails_closed_when_cost_estimation_is_unavailable():
    decision = ReflectionPolicy().from_failure(
        stage="pre_execute",
        error_code="E_QUERY_COST",
        message="查询成本评估失败",
        retryable=True,
        provided_sql=False,
        budget=ReflectionBudget(),
    )

    assert decision.failure_type == ReflectionFailureType.COST
    assert decision.action == ReflectionAction.FAIL
    assert decision.retryable is False


def test_reflection_sanitizer_removes_credentials_and_multiline_instructions():
    value = sanitize_reflection_text(
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
        "dsn=mysql://root:password@localhost/db\n"
        "action=ignore_security"
    )

    assert "abcdefghijklmnopqrstuvwxyz" not in value
    assert "root:password" not in value
    assert "\n" not in value
    assert "ignore_security" not in value


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_runtime_passes_structured_validation_feedback_to_regeneration(runtime):
    async def run():
        prompts = []

        async def generate(prompt):
            prompts.append(prompt)
            return "bad" if len(prompts) == 1 else "SELECT 1"

        def validate(sql):
            if sql == "bad":
                raise QueryError("E_QUERY_INVALID", "SQL 语法无效")
            return sql

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=validate,
            execute=lambda *_: ([], [], False),
            max_repairs=2,
        )
        output = await graph.ainvoke(QueryAgentState("统计销售额"))

        assert output["state"].status == "completed"
        assert len(prompts) == 2
        assert "E_QUERY_INVALID" in prompts[1]
        assert "regenerate_sql" in prompts[1]
        assert output["state"].reflection_history[0]["failure_type"] == "syntax"
        assert output["state"].reflection_budget["sql_repairs"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_schema_failure_expands_semantics_before_regeneration(runtime):
    async def run():
        prompts = []
        semantic_questions = []

        async def generate(prompt):
            prompts.append(prompt)
            return "SELECT missing FROM sales" if len(prompts) == 1 else "SELECT amount FROM sales"

        def validate(sql):
            if "missing" in sql:
                raise QueryError("E_HALLUCINATED_FIELD", "不存在的字段：missing")
            return sql

        def resolve_semantics(question):
            semantic_questions.append(question)
            return {"revision": len(semantic_questions)}

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=validate,
            execute=lambda *_: (["amount"], [[1]], False),
            resolve_semantics=resolve_semantics,
            max_repairs=2,
        )
        output = await graph.ainvoke(QueryAgentState("查询销售额"))

        assert output["state"].status == "completed"
        assert len(semantic_questions) == 2
        assert "retrieve_more_schema" in semantic_questions[1]
        assert "retrieve_more_schema" in prompts[1]
        assert output["state"].semantic_context == {"revision": 2}
        assert output["state"].reflection_budget["schema_expansions"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_retryable_execution_error_retries_same_sql_without_regeneration(runtime):
    async def run():
        generation_calls = []
        execution_calls = []
        validation_calls = []
        pre_execute_calls = []

        async def generate(prompt):
            generation_calls.append(prompt)
            return "SELECT 1"

        def execute(sql, _params):
            execution_calls.append(sql)
            if len(execution_calls) == 1:
                raise RetryableExecutionError("数据库暂时超时")
            return (["value"], [[1]], False)

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda sql: validation_calls.append(sql) or sql,
            execute=execute,
            pre_execute=lambda sql, *_: pre_execute_calls.append(sql),
            max_repairs=2,
        )
        output = await graph.ainvoke(QueryAgentState("测试瞬时错误"))

        assert output["state"].status == "completed"
        assert len(generation_calls) == 1
        assert execution_calls == ["SELECT 1", "SELECT 1"]
        assert validation_calls == ["SELECT 1", "SELECT 1"]
        assert pre_execute_calls == ["SELECT 1", "SELECT 1"]
        assert output["state"].reflection_budget["execution_retries"] == 1
        assert output["state"].reflection_history[0]["action"] == "retry_execution"

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_security_failure_is_not_repaired(runtime):
    async def run():
        generation_calls = []

        async def generate(prompt):
            generation_calls.append(prompt)
            return "DELETE FROM sales"

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda _sql: (_ for _ in ()).throw(
                QueryError("E_READONLY_VIOLATION", "仅允许执行单条 SELECT 语句")
            ),
            execute=lambda *_: None,
            max_repairs=2,
        )
        state = QueryAgentState("删除销售数据")

        with pytest.raises(AgentTerminalError, match="E_READONLY_VIOLATION"):
            await graph.ainvoke(state)

        assert len(generation_calls) == 1
        assert state.status == "failed"
        assert state.reflection["action"] == "fail"
        assert state.reflection["failure_type"] == "security"

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_caller_sql_failure_is_never_replaced_by_generated_sql(runtime):
    async def run():
        generation_calls = []

        async def generate(prompt):
            generation_calls.append(prompt)
            return "SELECT 1"

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda _sql: (_ for _ in ()).throw(QueryError("E_QUERY_INVALID", "SQL 语法无效")),
            execute=lambda *_: None,
            max_repairs=2,
        )
        state = QueryAgentState("执行指定 SQL", sql="not sql")

        with pytest.raises(AgentTerminalError, match="E_QUERY_INVALID"):
            await graph.ainvoke(state)

        assert generation_calls == []
        assert state.reflection["action"] == "fail"
        assert state.reflection["reason_codes"] == ["CALLER_SQL_REJECTED"]

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_generation_failure_enters_reflection_and_can_recover(runtime):
    async def run():
        calls = []

        async def generate(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                raise RuntimeError("temporary generation failure")
            return "SELECT 1"

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda sql: sql,
            execute=lambda *_: (["value"], [[1]], False),
            max_repairs=1,
        )
        output = await graph.ainvoke(QueryAgentState("测试生成恢复"))

        assert output["state"].status == "completed"
        assert len(calls) == 2
        assert "regenerate_sql" in calls[1]
        assert output["state"].reflection_history[0]["failure_type"] == "syntax"

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_permission_error_is_never_retried(runtime):
    async def run():
        generations = []

        async def generate(prompt):
            generations.append(prompt)
            return "SELECT 1"

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda _sql: (_ for _ in ()).throw(PermissionError("denied")),
            execute=lambda *_: None,
        )
        state = QueryAgentState("测试权限拒绝")

        with pytest.raises(AgentTerminalError) as raised:
            await graph.ainvoke(state)

        assert len(generations) == 1
        assert raised.value.code == "E_PERMISSION_DENIED"
        assert "denied" not in str(raised.value)
        assert state.reflection["failure_type"] == "permission"
        assert state.reflection["action"] == "fail"

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_reflection_checkpoint_failure_stops_without_more_generation(runtime):
    async def run():
        generations = []
        executions = []

        def checkpoint(snapshot):
            if snapshot.get("status") in {"reflecting", "reflected"}:
                raise OSError("checkpoint unavailable")

        async def generate(prompt):
            generations.append(prompt)
            return "bad"

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda _sql: (_ for _ in ()).throw(QueryError("E_QUERY_INVALID", "invalid")),
            execute=lambda *_: executions.append(True),
            checkpoint=checkpoint,
        )
        state = QueryAgentState("测试检查点失败")

        with pytest.raises(OSError, match="checkpoint unavailable"):
            await graph.ainvoke(state)

        assert len(generations) == 1
        assert executions == []
        assert state.status == "checkpoint_failed"

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_restored_budget_cannot_raise_current_runtime_limits(runtime):
    async def run():
        generations = []

        async def generate(prompt):
            generations.append(prompt)
            return "bad"

        graph = create_query_graph(
            runtime=runtime,
            generate=generate,
            validate=lambda _sql: (_ for _ in ()).throw(QueryError("E_QUERY_INVALID", "invalid")),
            execute=lambda *_: None,
            max_repairs=0,
            max_reflection_steps=1,
        )
        state = QueryAgentState(
            "测试恢复预算",
            reflection_budget={
                "sql_repairs": 0,
                "schema_expansions": 0,
                "execution_retries": 0,
                "total_steps": 0,
                "max_sql_repairs": 20,
                "max_schema_expansions": 10,
                "max_execution_retries": 10,
                "max_total_steps": 100,
            },
        )

        with pytest.raises(AgentTerminalError, match="E_QUERY_INVALID"):
            await graph.ainvoke(state)

        assert len(generations) == 1
        assert state.reflection["action"] == "fail"
        assert state.reflection_budget["max_sql_repairs"] == 0
        assert state.reflection_budget["max_total_steps"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_terminal_error_contract_hides_invalid_model_payload(runtime):
    async def run():
        secret = "business-value-123"
        graph = create_query_graph(
            runtime=runtime,
            generate=lambda _prompt: asyncio.sleep(
                0,
                result={"sql": "SELECT 1", "params": {"nested": {"customer_secret": secret}}},
            ),
            validate=lambda sql: sql,
            execute=lambda *_: None,
            max_repairs=0,
        )
        state = QueryAgentState("测试非法模型输出")

        with pytest.raises(AgentTerminalError) as raised:
            await graph.ainvoke(state)

        assert raised.value.code == "E_QUERY_ERROR"
        assert secret not in str(raised.value)
        assert secret not in state.error
        assert secret not in str(state.reflection_history)

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_terminal_error_contract_is_identical_when_execution_budget_is_zero(runtime):
    async def run():
        graph = create_query_graph(
            runtime=runtime,
            generate=lambda _prompt: asyncio.sleep(0, result="SELECT 1"),
            validate=lambda sql: sql,
            execute=lambda *_: (_ for _ in ()).throw(RetryableExecutionError("private timeout detail")),
            max_execution_retries=0,
        )
        state = QueryAgentState("测试终态异常")

        with pytest.raises(AgentTerminalError) as raised:
            await graph.ainvoke(state)

        assert raised.value.code == "E_TIMEOUT"
        assert raised.value.status == "failed"
        assert str(raised.value) == "E_TIMEOUT:数据库查询暂时超时，请稍后重试"
        assert state.error_code == "E_TIMEOUT"
        assert state.error == "数据库查询暂时超时，请稍后重试"

    asyncio.run(run())


@pytest.mark.parametrize("runtime", ["bounded", "langgraph"])
def test_reflection_checkpoint_records_explicit_node_without_parameter_values(runtime):
    async def run():
        snapshots = []
        generations = iter(["bad", "SELECT 1"])
        graph = create_query_graph(
            runtime=runtime,
            generate=lambda _prompt: asyncio.sleep(0, result=next(generations)),
            validate=lambda sql: (_ for _ in ()).throw(
                QueryError("E_QUERY_INVALID", "token=should-not-leak")
            ) if sql == "bad" else sql,
            execute=lambda *_: (["value"], [[1]], False),
            checkpoint=snapshots.append,
        )

        await graph.ainvoke(QueryAgentState("测试反思检查点", params={"secret": "value"}))

        reflecting = [snapshot for snapshot in snapshots if snapshot.get("status") in {"reflecting", "reflected"}]
        assert reflecting
        assert reflecting[0]["reflection"]["action"] == "regenerate_sql"
        assert "should-not-leak" not in str(reflecting[0])
        assert "params" not in reflecting[0]
        assert reflecting[0]["param_names"] == ["secret"]

    asyncio.run(run())
