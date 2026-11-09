import asyncio

from app.services.agent_query_service import AgentQueryService


def test_agent_query_service_uses_direct_provider():
    async def direct(question, schema, **kwargs):
        return kwargs["dialect"] + ":SELECT 1"

    service = AgentQueryService(provider="direct", direct_generate=direct)
    result = asyncio.run(service.generate(question="q", schema={}, context={}, dialect="sqlite", direct_kwargs={"api_key": "k"}))
    assert result == "sqlite:SELECT 1"


def test_agent_query_service_builds_langchain_provider():
    calls = []
    async def langchain_generate(**kwargs):
        calls.append(kwargs["generator"])
        return {"sql": "SELECT 1"}

    service = AgentQueryService(provider="langchain", direct_generate=None, langchain_generate=langchain_generate, build_langchain=lambda **config: ("generator", config), model="m")
    result = asyncio.run(service.generate(question="q", schema={}, context=None, dialect="mysql", direct_kwargs={}))
    assert result == {"sql": "SELECT 1"}
    assert calls[0][0] == "generator"
