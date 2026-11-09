import asyncio
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate
import langchain_openai

from app.agent.langchain_adapter import build_sql_generator, generate_sql
from app.agent.contracts import parse_sql_plan


def test_langchain_generator_normalizes_sql_output():
    class FakeGenerator:
        async def ainvoke(self, payload):
            assert payload["dialect"] == "mysql"
            return "```sql\nSELECT 1\n```"

    assert asyncio.run(generate_sql(generator=FakeGenerator(), question="q", schema={"sales": {"id"}}, context=None, dialect="mysql")).sql == "SELECT 1"

def test_structured_sql_plan_supports_json_and_plain_sql():
    plan = parse_sql_plan('{"sql":"SELECT 1","intent":"sales","confidence":0.9,"needs_approval":true}')
    assert plan.intent == "sales" and plan.needs_approval is True
    assert parse_sql_plan("SELECT 1").sql == "SELECT 1"

def test_langchain_generator_accepts_structured_plan_object():
    class FakeGenerator:
        async def ainvoke(self, payload):
            return {"sql": "SELECT 1", "intent": "summary", "confidence": 0.8, "needs_approval": False}

    assert asyncio.run(generate_sql(generator=FakeGenerator(), question="q", schema={}, context=None, dialect="mysql")).sql == "SELECT 1"


def test_langchain_generator_extracts_usage_metadata():
    class Message:
        content = "SELECT 1"
        usage_metadata = {"input_tokens": 21, "output_tokens": 8}

    class FakeGenerator:
        async def ainvoke(self, payload):
            return Message()

    usage = {}
    plan = asyncio.run(generate_sql(generator=FakeGenerator(), question="q", schema={}, context=None, dialect="mysql", usage_callback=usage.update))
    assert plan.sql == "SELECT 1"
    assert usage == {"input_tokens": 21, "output_tokens": 8}


def test_langchain_generator_extracts_response_metadata_usage():
    class Message:
        content = "SELECT 1"
        usage_metadata = None
        response_metadata = {"token_usage": {"prompt_tokens": 11, "completion_tokens": 4}}

    class FakeGenerator:
        async def ainvoke(self, payload):
            return Message()

    usage = {}
    asyncio.run(generate_sql(generator=FakeGenerator(), question="q", schema={}, context=None, dialect="mysql", usage_callback=usage.update))
    assert usage == {"input_tokens": 11, "output_tokens": 4}


def test_build_generator_treats_tenant_prompt_braces_as_literal(monkeypatch):
    captured = {}

    class FakePrompt:
        def __or__(self, _other):
            return self

    class FakeLlm:
        def with_structured_output(self, _schema):
            return object()

    def capture(messages):
        captured["messages"] = messages
        return FakePrompt()

    monkeypatch.setattr(ChatPromptTemplate, "from_messages", capture)
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **_kwargs: FakeLlm())
    build_sql_generator(api_key="key", base_url="https://api.example.com", model="model", timeout=5, system_prompt="保留字面量 {region}", prompt_version="sql_generation-v3")

    system_message = captured["messages"][0]
    assert isinstance(system_message, SystemMessage)
    assert "{region}" in system_message.content
    assert "sql_generation-v3" in system_message.content
