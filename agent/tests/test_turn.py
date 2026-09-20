"""A whole turn, against the real toolset and the real transport, with no model anywhere.

The model's contribution to a turn is two things: which sections to fetch, and the remark in
the assistant part. Both are supplied here directly. Everything between them and the rendered
answer is what this package is, and all of it is deterministic.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.tool_context import ToolContext

from verifiable_answer_agent.audit import ToolCallRecord
from verifiable_answer_agent.contract import ToolResult
from verifiable_answer_agent.postcheck import VerifyQuote
from verifiable_answer_agent.tools import (
    TURN_ID_STATE_KEY,
    build_query_toolset,
    read_tool_result,
    record_for,
)
from verifiable_answer_agent.turn import answer_turn

from .conftest import config_for, invocation_context
from .fake_query_service import FakeQueryService, running_query_service

SECTION_KEYS = ["smpc.4.3", "smpc.4.4"]
TURN_ID = "0f6b3a2e-4c1d-4e8f-9a7b-1c2d3e4f5a6b"


class Wiring:
    """Holds the toolset, the ADK contexts, and the calls made, for one test."""

    def __init__(self, toolset: McpToolset, tools: dict[str, BaseTool], context: Any) -> None:
        self.toolset = toolset
        self.tools = tools
        self.context = context
        self.calls: list[ToolCallRecord] = []

    async def get_sections(self, keys: Sequence[str]) -> list[ToolResult]:
        results: list[ToolResult] = []
        for key in keys:
            started = time.monotonic()
            raw = await self.tools["get_section"].run_async(
                args={"bundleId": "synthetic-smpc", "sourceKey": key},
                tool_context=ToolContext(self.context),
            )
            result = read_tool_result("get_section", raw)
            self.calls.append(record_for("get_section", result, started))
            results.append(result)
        return results

    @property
    def verify_quote(self) -> VerifyQuote:
        async def verify(
            bundle_id: str, version_id: str, source_key: str, quote: str
        ) -> ToolResult:
            started = time.monotonic()
            raw = await self.tools["verify_quote"].run_async(
                args={
                    "bundleId": bundle_id,
                    "versionId": version_id,
                    "sourceKey": source_key,
                    "quote": quote,
                },
                tool_context=ToolContext(self.context),
            )
            result = read_tool_result("verify_quote", raw)
            self.calls.append(record_for("verify_quote", result, started))
            return result

        return verify


@contextlib.asynccontextmanager
async def wired(service: FakeQueryService) -> AsyncIterator[Wiring]:
    toolset = build_query_toolset(config_for(service.url))
    # What begin_turn does at the start of a real invocation: the id is in state before the
    # first request, so every request of the turn carries it.
    context = invocation_context(turn_id=TURN_ID)
    try:
        tools = {tool.name: tool for tool in await toolset.get_tools(ReadonlyContext(context))}
        yield Wiring(toolset, tools, context)
    finally:
        with contextlib.suppress(Exception):
            await toolset.close()


@pytest.mark.parametrize("surface", ["a2ui", "text"])
async def test_an_honest_turn_verifies_every_block(
    query_service: FakeQueryService, surface: Any
) -> None:
    async with wired(query_service) as wiring:
        sections = await wiring.get_sections(SECTION_KEYS)
        turn = await answer_turn(
            section_results=sections,
            assistant_text="Two sections are relevant.",
            verify_quote=wiring.verify_quote,
            surface=surface,
            principal="urn:reviewer:synthetic-01",
            service_version="agent/0.1.0",
            tool_calls=wiring.calls,
            turn_id=wiring.context.session.state[TURN_ID_STATE_KEY],
        )

    assert turn.audit.turn_id == TURN_ID
    assert set(query_service.seen_turn_id) == {TURN_ID}
    assert len(turn.answer.blocks) == len(SECTION_KEYS)
    assert turn.answer.flagged_blocks == ()
    assert turn.audit.spans_verified == len(SECTION_KEYS)
    assert turn.audit.spans_flagged == 0
    assert {call.tool for call in turn.audit.tools} == {"get_section", "verify_quote"}
    assert all(call.outcome == "ok" for call in turn.audit.tools)


async def test_a_block_the_store_no_longer_contains_is_flagged_on_the_card() -> None:
    # The store has moved on since composition: this section no longer matches anything.
    with running_query_service(corrupt_section="smpc.4.3") as service:
        async with wired(service) as wiring:
            sections = await wiring.get_sections(SECTION_KEYS)
            turn = await answer_turn(
                section_results=sections,
                assistant_text="",
                verify_quote=wiring.verify_quote,
                surface="a2ui",
                principal="urn:reviewer:synthetic-01",
                service_version="agent/0.1.0",
                turn_id=TURN_ID,
                tool_calls=wiring.calls,
            )

    flagged = {block.citation.source_key for block in turn.answer.flagged_blocks}
    verified = {block.citation.source_key for block in turn.answer.verified_blocks}
    assert flagged == {"smpc.4.3"}
    assert verified == {"smpc.4.4"}
    assert turn.audit.spans_flagged == 1
    assert "no-match" in turn.audit.flags

    envelopes = turn.rendered
    assert isinstance(envelopes, list)
    update = next(item for item in envelopes if "updateComponents" in item)
    statuses = {
        item["id"]: item["text"]
        for item in update["updateComponents"]["components"]
        if item["id"].endswith("_status")
    }
    assert "NOT VERIFIED" in statuses["block-01_status"]
    assert "NOT VERIFIED" not in statuses["block-02_status"]


async def test_a_section_the_contract_refuses_never_reaches_the_answer() -> None:
    with running_query_service(break_schema_for="smpc.4.3") as service:
        async with wired(service) as wiring:
            sections = await wiring.get_sections(SECTION_KEYS)
            turn = await answer_turn(
                section_results=sections,
                assistant_text="",
                verify_quote=wiring.verify_quote,
                surface="text",
                principal="urn:reviewer:synthetic-01",
                service_version="agent/0.1.0",
                turn_id=TURN_ID,
                tool_calls=wiring.calls,
            )

    assert [block.citation.source_key for block in turn.answer.blocks] == ["smpc.4.4"]
    assert turn.audit.sections_dropped == 1
    assert "schema-invalid" in {call.outcome for call in turn.audit.tools}
    assert isinstance(turn.rendered, str)
    assert "smpc.4.3" not in turn.rendered
