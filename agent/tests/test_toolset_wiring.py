"""The real ADK toolset against a real MCP server: four tools, the user's token, no fallback."""

from __future__ import annotations

import contextlib

import pytest
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext

from verifiable_answer_agent.tools import (
    QUERY_TOOL_NAMES,
    MissingUserTokenError,
    bearer_header_provider,
    build_query_toolset,
    read_tool_result,
)

from .conftest import TEST_TOKEN, config_for, invocation_context
from .fake_query_service import FakeQueryService, running_query_service


async def _tools(toolset: McpToolset, context: ReadonlyContext) -> dict[str, BaseTool]:
    return {tool.name: tool for tool in await toolset.get_tools(context)}


async def test_the_toolset_exposes_exactly_the_four_contract_tools(
    query_service: FakeQueryService,
) -> None:
    toolset = build_query_toolset(config_for(query_service.url))
    context = invocation_context()
    try:
        tools = await _tools(toolset, ReadonlyContext(context))
        assert sorted(tools) == sorted(QUERY_TOOL_NAMES)
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()


async def test_the_end_users_token_reaches_the_service_on_every_request(
    query_service: FakeQueryService,
) -> None:
    toolset = build_query_toolset(config_for(query_service.url))
    context = invocation_context()
    try:
        tools = await _tools(toolset, ReadonlyContext(context))
        raw = await tools["get_section"].run_async(
            args={"bundleId": "synthetic-smpc", "sourceKey": "smpc.4.3"},
            tool_context=ToolContext(context),
        )
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()

    result = read_tool_result("get_section", raw)
    assert result.available
    assert query_service.seen_authorization, "the service saw no request at all"
    assert set(query_service.seen_authorization) == {f"Bearer {TEST_TOKEN}"}


async def test_a_missing_token_fails_the_call_closed(query_service: FakeQueryService) -> None:
    toolset = build_query_toolset(config_for(query_service.url))
    context = invocation_context(token=None)
    try:
        with pytest.raises(MissingUserTokenError):
            await toolset.get_tools(ReadonlyContext(context))
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()
    # Fail closed means nothing was attempted on the agent's own authority.
    assert query_service.seen_authorization == []


def test_the_header_provider_refuses_a_blank_token() -> None:
    for token in ("", "   "):
        context = invocation_context(token=token)
        with pytest.raises(MissingUserTokenError):
            bearer_header_provider(ReadonlyContext(context))


def test_the_header_provider_is_the_only_source_of_the_header() -> None:
    headers = bearer_header_provider(ReadonlyContext(invocation_context()))
    assert headers == {"Authorization": f"Bearer {TEST_TOKEN}"}


async def test_a_result_that_fails_the_contract_is_unavailable_not_content() -> None:
    with running_query_service(break_schema_for="smpc.4.3") as service:
        toolset = build_query_toolset(config_for(service.url))
        context = invocation_context()
        try:
            tools = await _tools(toolset, ReadonlyContext(context))
            bad = await tools["get_section"].run_async(
                args={"bundleId": "synthetic-smpc", "sourceKey": "smpc.4.3"},
                tool_context=ToolContext(context),
            )
            good = await tools["get_section"].run_async(
                args={"bundleId": "synthetic-smpc", "sourceKey": "smpc.4.4"},
                tool_context=ToolContext(context),
            )
        finally:
            with contextlib.suppress(Exception):
                await toolset.close()

    refused = read_tool_result("get_section", bad)
    assert not refused.available
    assert refused.reason == "schema-invalid"
    assert refused.value is None
    assert read_tool_result("get_section", good).available
