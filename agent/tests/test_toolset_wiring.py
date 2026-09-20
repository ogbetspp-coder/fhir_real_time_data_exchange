"""The real ADK toolset against a real MCP server: four tools, the user's token, no fallback."""

from __future__ import annotations

import contextlib
import uuid

import pytest
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext

from verifiable_answer_agent.tools import (
    QUERY_TOOL_NAMES,
    TURN_ID_HEADER,
    TURN_ID_STATE_KEY,
    InvalidTurnIdError,
    MissingUserTokenError,
    bearer_header_provider,
    begin_turn,
    build_query_toolset,
    current_turn_id,
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


TURN_ID = "0f6b3a2e-4c1d-4e8f-9a7b-1c2d3e4f5a6b"


def test_a_context_carrying_a_turn_id_sends_it_with_the_token() -> None:
    headers = bearer_header_provider(ReadonlyContext(invocation_context(turn_id=TURN_ID)))
    assert headers == {"Authorization": f"Bearer {TEST_TOKEN}", TURN_ID_HEADER: TURN_ID}


def test_a_context_without_a_turn_id_sends_the_token_alone() -> None:
    # The service accepts a call without the header; nothing is invented to fill it.
    headers = bearer_header_provider(ReadonlyContext(invocation_context()))
    assert TURN_ID_HEADER not in headers
    assert current_turn_id(ReadonlyContext(invocation_context())) is None


@pytest.mark.parametrize(
    "planted", ["synthetic-turn", "", 7, "0F6B3A2E-4C1D-4E8F-9A7B-1C2D3E4F5A6B "]
)
def test_a_turn_id_that_is_not_a_uuid_fails_the_call_closed(planted: object) -> None:
    with pytest.raises(InvalidTurnIdError):
        bearer_header_provider(ReadonlyContext(invocation_context(turn_id=planted)))


def test_the_missing_token_is_checked_before_the_turn_id() -> None:
    with pytest.raises(MissingUserTokenError):
        bearer_header_provider(ReadonlyContext(invocation_context(token=None, turn_id="bad")))


def test_begin_turn_puts_a_fresh_uuid_in_state_that_the_header_provider_then_sends() -> None:
    context = invocation_context()
    assert TURN_ID_STATE_KEY not in context.session.state
    assert begin_turn(CallbackContext(context)) is None
    first = current_turn_id(ReadonlyContext(context))
    assert first is not None
    assert str(uuid.UUID(first)) == first
    assert bearer_header_provider(ReadonlyContext(context))[TURN_ID_HEADER] == first
    # A second turn is a second id.
    begin_turn(CallbackContext(context))
    assert current_turn_id(ReadonlyContext(context)) != first
    assert TURN_ID_STATE_KEY.startswith("temp:")


async def test_the_turn_id_reaches_the_service_on_every_request_of_the_turn(
    query_service: FakeQueryService,
) -> None:
    toolset = build_query_toolset(config_for(query_service.url))
    context = invocation_context()
    begin_turn(CallbackContext(context))
    turn_id = current_turn_id(ReadonlyContext(context))
    try:
        tools = await _tools(toolset, ReadonlyContext(context))
        raw = await tools["find_product"].run_async(
            args={"query": "synthetic"}, tool_context=ToolContext(context)
        )
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()

    assert read_tool_result("find_product", raw).available
    assert query_service.seen_turn_id, "the service saw no request at all"
    assert set(query_service.seen_turn_id) == {turn_id}
    assert len(query_service.seen_turn_id) == len(query_service.seen_authorization)


async def test_a_truncated_product_search_is_available_and_says_so() -> None:
    with running_query_service(truncate_find_product=True) as service:
        toolset = build_query_toolset(config_for(service.url))
        context = invocation_context()
        try:
            tools = await _tools(toolset, ReadonlyContext(context))
            raw = await tools["find_product"].run_async(
                args={"query": "synthetic"}, tool_context=ToolContext(context)
            )
        finally:
            with contextlib.suppress(Exception):
                await toolset.close()

    result = read_tool_result("find_product", raw)
    assert result.available
    assert result.value is not None
    assert result.value["truncated"] is True


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
